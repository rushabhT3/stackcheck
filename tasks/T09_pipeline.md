# T09: Pipeline, jobs and the bounded agent

**Owner:** Muse Code. **Days:** Part A on D2 (skeleton), Part B on D4 (complete). **Read:** AGENTS.md; CONTRACTS C3 (`CheckStatus`, `StepStatus`, `Report`, `AgentStep`), C5 (`investigate_v1`, `summary_v1`), C15, C16.

## Goal

Part A: one command takes a project's PDFs to a `report.json`, end to end, even if most steps are thin. Part B: every step real, run as background jobs with live status, plus the bounded agent that tries to resolve undecided floor checks.

---

## Part A (D2): skeleton

### Files
Create: `src/stackcheck/pipeline/run_check.py`, `pipeline/jobs.py` (minimal), `report/build.py`, `scripts/run_project.py`, `tests/e2e/test_pipeline_smoke.py`.

### Steps
1. `run_check(check_id, project_label, sources, settings, *, clients=None)` with `clients` an optional bundle `{reader, vision, sam}` for tests. Steps `fetch` → `read_filings` → `rules` → `report` implemented; `photos`, `floors`, `investigate` marked `skipped` for now.
2. Write `status.json` (atomic) on every step change; write `evidence.json`, `findings.json`, `report.json` per C16 folder layout.
3. `report/build.py`: `build_report(...) -> Report` with verdict rule, costs from the ledger filtered by `check_id`, versions (`app`, each prompt version, each model id), `DISCLAIMER`, redaction per C16.
4. `scripts/run_project.py --label "Project A (Patna)" --csv data/sources.csv` (rows filtered by label) or `--pdf a.pdf b.pdf`; prints the verdict and the path of `report.json`.

### Tests first
`test_pipeline_smoke.py`: two synthetic PDFs (factories from T04) and fake clients returning valid `QuarterFiling` JSON for quarters ending 2025-03-31 and 2025-06-30 with slabs 3 then 2. Running `run_check` writes `report.json`; R4 FLAG high is present; status steps show `done` or `skipped`; no network used.

### Done when (G2)
The smoke test is green, and the human runs `run_project.py` live on one real filing and gets a report.

---

## Part B (D4): complete

### Files
Complete the above; create `src/stackcheck/pipeline/investigate.py`, `tests/unit/test_investigate.py`, `tests/unit/test_jobs.py`; extend the smoke test.

### Steps
1. Steps `photos` (select, save, `describe_photo`, `dhash_hex`) and `floors` (for elevations: SAM `"building"` plus each prompt in `settings.sam_prompts`, run concurrently with at most 4 SAM calls in flight per check; `count_floors`; overlay into `media/`).
2. Origin rule (D07): for each source, clients come from `reader_client(settings, origin)` and `vision_client(settings, origin)`.
3. `investigate.py` per C15 with `AgentChoice`. The program validates every choice:
   - action must be one of the four;
   - `retry_sam_prompt` argument must be in the allowed list (`["balcony", "window", "window opening", "concrete column", "floor slab"]`) and not yet tried for that photo;
   - `use_other_photo` argument must be another elevation image id from the same quarter and block;
   - `crop_to_building` argument must be the current image id and the photo must have a building box;
   - otherwise record a `give_up` step with result "invalid choice rejected by program" and stop.
   After each executed action, recompute that photo's `FloorCount`, rebuild that quarter's evidence and re-run R1 for that quarter and block only. The agent's text never reaches a finding.
4. `summary` (inside the `report` step): `summary_v1` in English always; Hindi only if `settings` has it enabled (S2). Failure leaves `summary_en = None` and logs; the check still finishes.
5. `jobs.py` per C16: `JobRunner(settings, *, run=run_check, on_finished=None)`, `submit(project_label, sources) -> check_id`, `status(check_id) -> CheckStatus`, `QueueFull`, restart recovery.
6. Progress = finished or skipped steps / 7.

### Tests first
`test_investigate.py` (fake vision client returning scripted `AgentChoice` JSON, fake SAM):
- disagreement, agent picks `retry_sam_prompt "window opening"`, new rows agree → R1 becomes decided, 1 step, loop stops;
- agent repeats an already tried prompt → one `give_up` step with the rejection text;
- agent names an image from another quarter → rejected;
- three valid but useless actions → stops after exactly 3 steps;
- the agent's JSON includes an extra key `"status": "FLAG"` → ignored, status still computed by R1;
- `give_up` stops immediately.

`test_jobs.py`:
- `submit` returns `chk_` + 10 hex; status goes queued → running → done with a fake `run`;
- with `max_concurrent_checks=1`, a second job waits until the first finishes;
- queue beyond `max_queue` raises `QueueFull`;
- `on_finished` called once per job, also on failure;
- a `status.json` left as `running` is marked `failed` on startup.

Extend the smoke test: photos and floors steps run with fake SAM output (a synthetic grid) and fake vision counts; the report contains an R1 finding with an overlay path that exists.

### Done when (G4)
All tests green; human runs a full real project (≥ 4 quarters) live and the report has findings with evidence, a trace (possibly empty) and costs. Record time per quarter and cost per check in PROGRESS.md.

## How an expert thinks about it

The skeleton on day 2 is the most important code in the project, because it turns every later card into "make one step real" instead of "integrate everything at the end". The agent is deliberately small: it only acts where the deterministic pipeline is stuck, it picks from a menu, and the program checks every pick. That's what makes it an agent you can trust, and it's exactly what the "verifiable agents" session is about.

## Traps

- Don't run SAM calls for every photo at once: cap concurrency, or rate limits and costs spike.
- Never let one bad photo fail the check: catch per-photo errors, record them as a note on that photo, continue.
- Status writes must be atomic (temp file plus `os.replace`), or the UI will read half-written JSON.
