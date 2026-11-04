# SPEC: what StackCheck is and what "done" means

## The problem

In India, a homebuyer usually pays most of the price of a flat years before it exists. RERA (the Real Estate Regulation and Development Act, 2016) answers this with transparency: every registered project must file a quarterly progress report (QPR) on its state's RERA portal. A QPR states how far construction has got, how much money came in and went out, and in many states it must include dated, geo-tagged photos of every block and floor.

Nobody checks these reports against each other. Buyers don't know they exist, the PDFs are dense, and a portal holds thousands of them. In Telangana, an estimated 30 to 40 percent of builders don't even upload them (Deccan Chronicle, July 2025). About 63,000 flats were running late in Hyderabad alone (ANAROCK, June 2026).

## The user

A homebuyer, or someone helping one (a relative, a buyers' association, a journalist), who has a RERA registration number and wants to know: "Does what the builder reports match what the builder's own photos and numbers show?"

## What StackCheck does

Input: the public QPR PDFs of one project (links to the RERA portal, or uploaded files), covering as many quarters as are available.

For every quarter it:
1. reads the filing into structured facts (claimed slabs, activity percentages, money received and spent, dates, certificates present), with a page reference for each fact;
2. pulls out the photos, sorts them into elevation, floor-level and other, and reads any date and location stamp printed on them;
3. counts the floors visible in each elevation photo with two independent methods (SAM 3.1 detections grouped into rows, and Muse Spark's own count) and keeps the result only when they agree;
4. runs deterministic checks across all quarters.

Output: a report with a headline verdict, one card per check, the evidence for each (page quotes, photo with drawn boxes and rows, the arithmetic), and a list of what it couldn't verify and why.

## The checks (plain words; exact formulas are in CONTRACTS)

| Code | What it catches |
|---|---|
| R1 PHOTO_VS_CLAIM | The builder claims N slabs, but the builder's own elevation photo for that quarter shows clearly fewer floors. |
| R2 REUSED_PHOTO | The same photo appears in two different quarters (near-duplicate image hash). |
| R3 PHOTO_DATE | The date stamped on a photo falls outside the quarter it is filed for. |
| R4 CLAIM_REGRESSION | Claimed progress goes backward between quarters. Floors can't disappear. |
| R5 COPY_PASTE_QUARTER | A quarter repeats the previous quarter's numbers, or reports spending with zero progress. |
| R6 MONEY_VS_PROGRESS | The share of money spent runs far ahead of the share of work done. Low severity: a question to ask, not a gap. |
| R7 ARITHMETIC | The money columns don't add up within a filing or across quarters. |
| R8 MISSING_QUARTER | A quarter between the first and last filing has no report. |

Each check returns OK, FLAG or CANT_VERIFY, with a severity and evidence.

## The demo moment (the 20 seconds a judge remembers)

A real project's filing says "slabs: 7 of 10". Next to it, the builder's own elevation photo from the same filing, with StackCheck's boxes drawn on each row of openings: 3 rows. A red card reads "Builder's photo shows 3 floors; report claims 7 slabs. Gap found." One click shows the same photo filed again two quarters later (R2). Then the "can't verify" list, which shows the tool knows its limits.

If the real data doesn't produce a gap, the demo shows the strongest real findings it does produce (R2, R3, R7 are common in practice). It never shows a made-up gap as real. Constructed examples are allowed only when labelled "constructed example" on screen.

## In scope

- QPR PDFs from Bihar RERA first (public URLs, photos required), with the ingest written so other states' PDFs work through upload.
- Elevation-photo floor counting. Floor-level photos are classified and used for R2 and R3 only.
- English report with an optional plain-language summary in Hindi (stretch S2).
- Provider switch for the reading step: Meta Model API, Baseten or Fireworks (Muse Spark Open), compared on accuracy and cost.
- Live deploy on Modal.

## Out of scope (don't build)

- Crawling or bulk-downloading any portal. Logging in to portals. Any captcha handling.
- User accounts, payments, notifications, saved watchlists.
- Training or fine-tuning models.
- Satellite, Street View or YouTube imagery.
- Legal advice or any wording that accuses a builder of wrongdoing.

## How this maps to the judging rubric

| Rubric (weight) | What StackCheck shows |
|---|---|
| Impact (30%) | Every under-construction RERA project in India files these reports; homebuyers' life savings are at stake; the check takes minutes and costs cents. |
| Meta AI tooling (25%) | SAM 3.1 for detection, Muse Spark 1.3 for document and photo reading, a bounded agent choosing what to try when a check can't decide, Muse Spark Open on Baseten/Fireworks compared head to head, the whole codebase written with Muse Code. |
| Technical depth (20%) | Two independent floor-count methods with an agreement rule, near-duplicate image hashing, deterministic rules with 100% branch tests, evidence tracing to page and pixel, a cost ledger. |
| Demo (25%) | Real public filings, a visible gap or a visible reused photo, boxes drawn on the builder's own photo. |

Bonus prizes it qualifies for without extra work: Best Build on Meta Open Weight Models (if Muse Spark Open on Baseten or Fireworks is the default reader), Best Agentic Use-Case (bounded agent with a trace).

## Proving the work is new

The rules disqualify projects whose new work isn't clear. So:
- the repo is created on Oct 28, 2026 and every commit is made during the build window;
- `PROGRESS.md` logs every task with a date;
- the README has a "Built during the hackathon" section listing every module;
- the demo video says it out loud in the first 15 seconds.

## Definition of done (for the whole project)

1. A judge can open the live URL, pick a sample project, and see a full report in under 10 seconds (precomputed), or start a new check from QPR links and watch it run.
2. At least 5 real projects checked, every shown FLAG verified by hand.
3. Floor counting measured on the benchmark, with the numbers in the README.
4. Every KPI in `docs/KPIS.md` measured, with targets met or the miss explained.
5. Public repo, 3-minute video, X post, submission filed before Nov 4, 9 PM ET.
