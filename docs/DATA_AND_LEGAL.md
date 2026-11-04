# DATA AND LEGAL: where the data comes from and why it's allowed

This is a practical summary, not legal advice. It was checked on Oct 28, 2026. Two rules drive everything here:

1. The hackathon disqualifies projects that "use code, data, or assets you do not have the rights to".
2. Rushabh doesn't film anything. All data comes from online sources that allow this use.

## Source verdicts

| Source | Verdict | Why |
|---|---|---|
| RERA quarterly progress reports (QPR PDFs) on state portals | **Use** (main source) | Statutory public filings, published by the regulator so the public can check project progress. Facts in them (percentages, amounts, dates) aren't copyrightable. Reading and analysing them is research, review and criticism, which is fair dealing under Section 52(1)(a) of the Indian Copyright Act, 1957. |
| Photos inside those QPRs | **Use for analysis; show with care** | The builder owns the copyright in the photos. Analysing them and showing them in a report that reviews the filing falls under fair dealing for criticism or review. Belt and braces: don't commit them to the repo, credit the source wherever shown, and send the permission email below. |
| Wikimedia Commons images | **Use** (benchmark) | Clear licences (public domain, CC BY, CC BY-SA). Can be committed with attribution. Drawing boxes on a CC BY-SA image makes the overlay CC BY-SA too: note that in ATTRIBUTION.md. |
| Pexels photos and videos | **Use sparingly, manual download only** | The licence allows free use and modification with no credit needed. It forbids automated collection "including for machine learning", building a similar service, and distributing files on their own. So: download a handful by hand, use them inside the product demo, never commit them, never script downloads. |
| User uploads (a buyer's own photo, video or PDF) | **Use** | The user supplies their own material. Upload path sends them only to the standard (non-training) model tier. See CONTRACTS, privacy rule. |
| YouTube videos (including builders' progress videos) | **Don't use** | YouTube's terms (effective Jan 9, 2026) forbid downloading or reproducing content without permission, and forbid automated access. Screen recording is reproduction. Embedding a YouTube player for context is allowed, but StackCheck can't analyse those frames. |
| Builder websites, brochures, ads | **Don't use** | Copyrighted marketing material, not statutory filings. No fair-dealing need for it, since the QPRs carry the same kind of photos. |
| Google Maps, Street View, Google Earth | **Don't use** | Maps Platform terms forbid scraping and extracting imagery for this kind of analysis. |
| Satellite open data (Sentinel-2) | **Not useful** | 10 m pixels can't show floors. |

## Which portals and why Bihar comes first

- **Bihar RERA:** QPR Form 7 PDFs are public at plain URLs like `https://rera.bihar.gov.in/Progress_Doc/<file>.pdf`, with no captcha. Form 7 requires "geo tagged and date photograph" of every block's front, rear and side elevations and of each floor, the "Slabs of Super Structure" figure, and money received and spent per quarter and cumulative. Some filings also attach the architect's (Form 1) and engineer's (Form 2) certificates. This gives claim, photos and money in one document.
- **Uttar Pradesh RERA:** the project page has "View Physical and Financial Report", then "Physical Target", which shows project photos and videos. Use as the fallback if Bihar PDFs turn out to have no embedded photos.
- **Telangana RERA (Hyderabad):** QPRs must include site photographs, but the search page has a captcha. Rushabh may open and download a few filings by hand as a normal visitor and upload them. The app never touches the TG-RERA site itself.
- **Maharashtra (MahaRERA):** large (33,029 projects under construction), percentages and certificates public, photos not confirmed. Upload path only.

## Gate 0: confirm the photos are really in the PDFs (10 minutes, human)

I couldn't open the PDF images from my sandbox, so this is unconfirmed. Before any coding, Rushabh opens these three public filings in a browser and checks Section IX ("Geo tagged and date photograph"):

- https://rera.bihar.gov.in/Progress_Doc/RERAP08022024125722-104141030122025.pdf
- https://rera.bihar.gov.in/Progress_Doc/RERAP2311201700015-8004151502402025.pdf
- https://rera.bihar.gov.in/Progress_Doc/RERAP31201800101-202251050442023.pdf

Record in `PROGRESS.md`: for each file, whether photos are embedded, roughly how many, whether elevations are included, and whether a date stamp is visible on them.

**Decision rule:**
- Photos in at least 2 of 3 → Bihar is the main source. Go ahead.
- Photos in 0 or 1 → check UP RERA "Physical Target" for 2 projects. If photos are there, Rushabh saves them by hand for 5 projects and the app uses the upload path. If not, StackCheck's demo centres on the document checks (R4, R5, R6, R7, R8), with floor counting shown on the Commons benchmark only. The product still works; the demo moment changes.

## Fetch etiquette (built into the code, see CONTRACTS)

- The app fetches only single PDF URLs that a user gives it, from an allow-list of RERA hosts.
- One request at a time per host, at least 5 seconds apart, 60-second timeout, 30 MB size cap, `application/pdf` only, redirects only within the same host.
- A clear User-Agent: `StackCheck/0.1 (hackathon research; contact via GitHub)`.
- No crawling, no listing pages, no captcha, no login.
- Every fetched file is stored under `data/work/` (git-ignored) with its URL, time and SHA-256, so every finding can be traced back to the exact bytes.

## Repo hygiene (public repo)

Committed:
- `data/sources.csv`: project label, state, official URL of each QPR, quarter.
- `data/bench/images/`: Wikimedia Commons files only, each listed in `data/bench/ATTRIBUTION.md` with title, author, licence and URL.
- `data/bench/labels.csv`: hand-counted floors.
- Derived facts and reports for sample projects, with photos replaced by their URL and page number (no image bytes).

Never committed (enforced by `.gitignore` and a test): PDFs, extracted filing photos, overlays of filing photos, Pexels files, `.env`.

## Wording that keeps everyone safe

Defamation is both a civil and a criminal matter in India, so every user-facing sentence must be factual and checkable:

- Say "The builder's photo filed for Q2 2025 shows 3 floors. The same report claims 7 slabs." Don't say "the builder lied".
- Say "gap found", "doesn't match", "can't verify". Never "fraud", "scam", "cheating".
- In the public demo video and README, call projects "Project A (Patna)" and blur promoter names. Keep the real registration numbers in a private evidence log, to show judges if asked.
- Each report says at the bottom: "StackCheck compares public RERA filings with each other. A gap is a question to ask the builder or RERA, not proof of wrongdoing."

## Permission email (optional extra cover, send today)

The Bihar RERA copyright page says material may be reproduced after asking by email, accurately and with credit. Send this from your own address to the contact listed on https://rera.bihar.gov.in/Copyright.aspx:

```text
Subject: Request to reproduce public QPR extracts in a non-commercial research demo

Dear Sir or Madam,

I am building StackCheck, a free tool for homebuyers that compares a project's quarterly progress reports on the RERA Bihar portal with each other, for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026).

I would like to show short extracts from a few public QPRs (figures and site photographs) in a 3-minute demo video and a public report, with clear credit to RERA Bihar as the source. Project and promoter names will be blurred in the public video. Nothing will be altered or used in a misleading way.

Could you confirm whether this is acceptable, or tell me if any conditions apply?

Thank you,
Rushabh Trivedi
rushabhtrivedi03@gmail.com
```

No reply by Nov 3 doesn't block anything: fair dealing still covers the analysis. Keep the email and any reply.

## Risk table

| Risk | Likelihood | Impact | What we do |
|---|---|---|---|
| Bihar PDFs have no embedded photos | Unknown until Gate 0 | High for demo moment | UP RERA fallback, then document-first demo |
| A judge questions rights to QPR photos | Low | Medium | Fair dealing, no copies in repo, credit, permission email, blurred names |
| Builder objects to being named | Low | High | Anonymised in public material, factual wording, disclaimer |
| Portal blocks or rate-limits | Low | Low | 5 s spacing, cache everything, user can upload PDFs instead |
| Pexels terms breach | Very low | Low | Manual downloads, not committed, not used for training |
