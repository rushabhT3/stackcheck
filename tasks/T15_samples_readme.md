# T15: Real projects, samples, README, demo assets

**Owner:** Human leads; Muse Code writes scripts and the README draft. **Day:** D7. **Read:** SPEC "Definition of done"; DEMO in full; DATA_AND_LEGAL "Repo hygiene" and "Wording"; KPIS section B.

## Goal

Five real projects checked on the live app, every shown FLAG verified by hand, a README with measured numbers, and everything needed to record the video.

## Files

Create: `scripts/export_sample.py`, `data/samples.json`, `data/samples/verified.csv`, `data/samples/<check_id>/report.json` (stripped), `README.md` (full).

## Steps

1. Human: run the 5 projects from `data/sources.csv` on the **live** app (so their media lives on the Modal Volume). Note each `check_id`.
2. Human: for every FLAG, open the PDF page and the photo and confirm by eye. Record in `data/samples/verified.csv`: `check_id,finding_id,verified(yes/no),note`. Any FLAG marked "no" is a bug: write it in STUCK.md and fix the rule or threshold before showing that project.
3. `scripts/export_sample.py <check_id> --from-url https://<live>/api/checks/<id>/report`: downloads the report JSON, sets every `PhotoRecord.overlay_path` and `Evidence.overlay_path` to null and every `ExtractedImage.path` to `""`, keeps `evidence.quarters[].source.url` and all page references, and writes `data/samples/<check_id>/report.json` (the live app keeps the full version on the Volume). Also appends `{"check_id", "project_label", "headline"}` to `data/samples.json`. Human uploads the updated `samples.json` to the Volume (`deploy/README.md`).
4. README per DEMO "README outline", filled with numbers from `docs/kpi_snapshot.md`. Every number has a date. The "Built during the hackathon" section lists modules with dates from PROGRESS.md.
5. Demo assets: 3 screenshots (report top, a FLAG with its overlay, the trace) saved under `docs/img/` only if they show no unredacted names and no filing photo (use a Commons benchmark overlay for any photo screenshot committed to the repo). The video itself may show filing photos with credit (fair dealing for review), with names blurred.
6. Run REVIEW_FOR_OPUS and apply its blockers.

## Tests first

`tests/unit/test_export_sample.py`: stripping removes all media paths and keeps page refs; appending to `samples.json` doesn't duplicate an existing `check_id`.

## Done when (G7)

5 projects live as samples (or as many as the data allowed, with the reason), 100% of shown FLAGs verified, README complete, Opus blockers fixed, video recorded per DEMO.

## How an expert thinks about it

The submission is judged on what a stranger can verify in a few minutes. Hand-verifying every FLAG is the cheapest insurance there is: one wrong red card in the video undoes everything else. And the README's numbers, with dates, are what separates "it seems to work" from "we measured it".

## Traps

- Don't commit filing photos or overlays of them. Screenshots for the repo use Commons images.
- Don't round accuracy up. 78% is 78%.
