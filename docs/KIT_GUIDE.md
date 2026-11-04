# KIT GUIDE: how Rushabh uses this kit

## What's in the kit

| File | For whom | What it does |
|---|---|---|
| `AGENTS.md` | Muse Code | Standing rules: the loop, hard rules, coding standards, stuck protocol, report format |
| `A1_PROMPT.md` | You | The corrected request this kit answers |
| `docs/SPEC.md` | Both | What StackCheck is, the checks, the demo moment, scope, definition of done |
| `docs/DATA_AND_LEGAL.md` | You first | Which data is allowed and why, Gate 0, permission email, safe wording |
| `docs/ARCHITECTURE.md` | Muse Code | Modules, decision log (D01 to D15), approved dependencies, repo layout |
| `docs/CONTRACTS.md` | Muse Code | Exact models, prompts, SAM grammar, floor algorithm, rule formulas, API, design tokens |
| `docs/FACTS.md` | Both | What was verified with sources, and the UNVERIFIED list with probes and fallbacks |
| `docs/SETUP.md` | You | Accounts, tools, running Muse Code, deploy |
| `docs/PLAN.md` | You | Day-by-day plan with a gate and fallback per day |
| `docs/KPIS.md` | You | Build-health and quality KPIs, red flags, credit plan |
| `docs/DEMO.md` | You | Video script, README outline, X post, submission checklist |
| `docs/REVIEW_FOR_OPUS.md` | Opus | Read-only review prompt and known weak spots |
| `tasks/` | Muse Code (T00 is yours) | 16 task cards in build order, plus 3 stretch cards |

## Today (Oct 28), in order

1. Read `docs/DATA_AND_LEGAL.md` (10 minutes).
2. Do `tasks/T00_gate0_data.md` yourself (about 2 hours, mostly finding filings and labelling benchmark photos). Send the permission email.
3. Create the public repo, copy the kit in, commit (SETUP section 3).
4. Run Muse Code on T01, then T02, then T03 (SETUP section 4). Commit after each.
5. End of day: check gate G1 in `docs/PLAN.md`.

## The daily loop

1. Morning: read `PROGRESS.md` and `STUCK.md`. Pick today's cards from PLAN.
2. For each card: new Muse Code session, first message from SETUP section 4, review the diff, run the tests yourself, commit.
3. When a card asks for a live probe or eval, run it yourself and paste the output into the session.
4. Evening: `uv run python scripts/kpi_report.py`, compare with the gate, apply the fallback if the gate failed.

## How to keep a weaker model on track

- One card per session. The card, AGENTS.md and the named CONTRACTS sections are all it needs.
- If it starts inventing (new dependency, renamed field, "I'll also add..."), stop it and paste: "Re-read AGENTS.md section 4. Undo changes outside the card."
- If it's stuck twice on the same thing, don't let it loop. Ask it to write STUCK.md, then bring that file to Opus or to me.
- Never let it edit `docs/CONTRACTS.md`. If a contract is truly wrong, change it yourself (or with Opus), add a line to the ARCHITECTURE decision log, then rerun the card.

## Where the judgement calls are pre-made

| Question the coder might face | Answer is in |
|---|---|
| Which SAM prompts? | T03 decides by measurement |
| When is a floor count trusted? | CONTRACTS C11 |
| When is a gap a gap? | CONTRACTS C14 |
| What can the agent do? | CONTRACTS C15 |
| Which provider reads filings? | T12 decides by measurement (D06) |
| What wording is allowed? | AGENTS.md section 4, DATA_AND_LEGAL |
| What happens when a day goes wrong? | PLAN, "If the gate fails" column |
