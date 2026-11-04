# T00: Gate 0 and the data you need (HUMAN task, about 2 hours)

**Owner:** Rushabh (not Muse Code). **Day:** D1, first thing. **Read:** `docs/DATA_AND_LEGAL.md`.

## Goal

Confirm the main data source works, collect the real filings and the benchmark, and set up spending alerts, before any code depends on them.

## Steps

### 1. Gate 0: are the photos in the PDFs? (10 minutes)

Open the three Bihar filings listed in DATA_AND_LEGAL "Gate 0" in your browser. For each, note in a scratch file: photos embedded (yes/no), how many, any elevations (whole building from outside), any date stamp visible. Apply the decision rule from DATA_AND_LEGAL.

### 2. Pick 5 projects (60 minutes)

Look for projects that have:
- at least 4 quarterly filings (more is better);
- elevation photos where the whole building is visible;
- one building (or clearly labelled blocks);
- 5 or more floors planned (G+4 and up), so counting is meaningful.

How to find them: on rera.bihar.gov.in, open registered projects and their quarterly progress documents. You can also search the web for `site:rera.bihar.gov.in Progress_Doc` and open results by hand. Do this by hand in your browser. Never use a script to list or download portal pages.

Write each filing into `data/sources.csv` (create it after T01, or keep the rows ready):

```text
project_label,state,quarter_end,url
Project A (Patna),Bihar,2025-03-31,https://rera.bihar.gov.in/Progress_Doc/....pdf
```

Use neutral labels ("Project A (Patna)"). Keep a private note (not in the repo) mapping labels to real names and registration numbers.

### 3. Benchmark photos (40 minutes)

From Wikimedia Commons, download 30 to 40 photos of buildings with clearly visible storeys: half under construction (concrete frames), half finished residential towers; vary angle, distance and light. Only files whose licence is public domain, CC0, CC BY or CC BY-SA.

- Save as `data/bench/images/b01.jpg`, `b02.jpg`, ...
- `data/bench/labels.csv`: `file,storeys_visible,top_obscured,notes`. Count storeys you can see, including the ground floor. Count each photo twice, a few minutes apart; if your two counts differ, write the lower and put "unsure" in notes.
- `data/bench/ATTRIBUTION.md`: one line per file: file name, title, author, licence, source URL. Add: "Overlays drawn by StackCheck on CC BY-SA images are shared under CC BY-SA."

### 4. Spending alerts (5 minutes)

On the Meta Model API Billing page: confirm the $150 credit appears, then set spend notifications at $50, $100 and $140.

### 5. Permission email (5 minutes, optional)

Send the email from DATA_AND_LEGAL to the contact on the Bihar RERA copyright page. Save it in your private notes.

### 6. Record the result

After T01 creates `PROGRESS.md`, add:

```text
## T00 Gate 0 and data: DONE
Gate 0: photos embedded in <n>/3 files; elevations: <yes/no>; date stamps visible: <yes/no>
Decision: <Bihar main source | UP fallback | document-first>
Projects: <n> projects, <n> filings in data/sources.csv
Benchmark: <n> Commons photos labelled
```

## Done when

- Gate 0 decision recorded.
- At least 3 projects (target 5) with at least 4 filings each are listed.
- At least 30 benchmark photos are labelled with attribution.
- Billing alerts are set.

## Traps

- Don't use YouTube, builder sites, Street View or Pexels for the benchmark. Commons only, because those files can be committed.
- Don't download filings with a script, and don't try to get around any captcha. Opening and saving by hand is normal use.
- A photo of a finished building with closed windows still counts: storeys are visible.
