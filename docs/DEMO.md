# DEMO: video, README, X post, submission

Judges watch the video first and decide in the first minute. Demo is 25% of the score, and the video is "one of the primary artifacts". Everything below serves one goal: a judge sees the builder's own photo with StackCheck's rows drawn on it, next to the builder's own claim, within 60 seconds.

## 3-minute video script (hard cap 3:00, aim for 2:45)

Record the screen at 1080p, voice over in English, captions on. Blur project and promoter names. Show "Built during the hackathon, Oct 28 to Nov 4, 2026" on the first frame.

| Time | Screen | Voice (say it like you'd tell a friend) |
|---|---|---|
| 0:00–0:15 | Title card, then a Hyderabad skyline still (Commons) | "In India you pay for a flat years before it exists. Every quarter, the builder files a progress report with RERA. Nobody checks those reports against each other. StackCheck does. Everything you'll see was built during this hackathon." |
| 0:15–0:35 | A real QPR PDF scrolling: the slab claim, then the photo section | "This is a real public filing. The builder says how many slabs are done, how much money came in and went out, and attaches dated photos of the building. Claim, photos and money, in one document." |
| 0:35–1:10 | StackCheck start page: paste 6 QPR links, press Check; progress steps tick | "I give StackCheck one project's filings. Muse Spark 1.3 reads each one into structured facts with page numbers. SAM 3.1 finds every row of windows and balconies in the builder's own photos, and Muse Spark counts storeys separately. We only trust a count when both agree." |
| 1:10–1:45 | Report: red card R1 or R2; overlay photo with numbered rows; click to evidence | **The demo moment.** "Here the report claims seven slabs. The builder's own photo, filed with that same report, shows three floors. StackCheck draws every row it counted, and links to the page with the claim." (If the strongest real finding is R2: "And this exact photo was filed again two quarters later.") |
| 1:45–2:10 | Can't-verify list; agent trace drawer | "When the evidence isn't clear, StackCheck says so. Here, the two counts disagreed, so a small agent chose what to try next: another photo, another SAM prompt. The program, not the model, decides the result, and every step is logged." |
| 2:10–2:30 | Provider table; cost badge | "The reading step runs on Muse Spark Open too, on Baseten and Fireworks. Same accuracy within N points, and a full project check costs about N cents." (Real numbers from T12 and the ledger.) |
| 2:30–2:45 | Benchmark numbers; repo | "On 30 openly licensed photos, the floor count is within one floor N% of the time, and it's confidently wrong N%. Code is open. Built with Muse Code." |
| 2:45–2:55 | Closing line on screen | "Every RERA project in India files these reports. StackCheck reads them so buyers don't have to." |

Rules for the video: every number on screen is real and comes from `docs/kpi_snapshot.md`. No made-up gap. If a constructed example is used anywhere, it says "constructed example" on screen.

## README outline (T15 writes it)

1. One-line pitch and a GIF of the report page.
2. "Try it": live URL and the sample projects.
3. What it checks: the R1 to R8 table in plain words.
4. How it works: the architecture picture from ARCHITECTURE, two paragraphs.
5. Meta models used and how: Muse Spark 1.3 (contributor for public filings, standard for uploads), SAM 3.1, Muse Spark Open on Baseten and Fireworks, Muse Code.
6. Measured results: benchmark table, extraction accuracy, provider comparison, cost per check. Dates of measurement.
7. Built during the hackathon: list of modules with the dates from PROGRESS.md.
8. Data and legal: short version of DATA_AND_LEGAL, the disclaimer, attribution file link, PyMuPDF AGPL note.
9. Run it yourself: SETUP short version.
10. Limits and what's next: honest list.

## X post (for the $20K Most Viral prize; post Nov 4 IST, before submitting)

```text
In India you pay for a flat years before it exists. Builders file progress reports with RERA every quarter, with photos.

I built StackCheck: it reads those reports and checks the builder's own photos against the builder's own numbers.

SAM 3.1 counts the floors. Muse Spark reads the filings. It says "can't verify" when it isn't sure.

Built in 8 days for the Meta Global AI Developer Hackathon, with Muse Code.
<live URL>  <repo URL>
```

Before posting, add the official handles the organisers use in their own posts (check the #announcements channel on Discord); don't guess handles.

Attach the 45-second cut (0:35 to 1:45 of the main video) and a screenshot of the overlay. Ask friends and buyer groups to share it. Engagement counts until Nov 8 end of day.

## Submission checklist (Nov 4)

- [ ] Repo public, README complete, no `.env`, no PDFs or filing photos committed (`git ls-files | grep -iE "\.pdf$|work/|ledger"` returns nothing).
- [ ] Video link opens in a private browser window.
- [ ] Live URL opens on a phone, sample report loads in under 10 seconds.
- [ ] X post link ready for the submission form.
- [ ] Track: Indie. Theme: "Build for Your World".
- [ ] Submitted on the CV platform before 10:00 PM IST, Nov 4.
