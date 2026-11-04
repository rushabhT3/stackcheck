# T16: Final cleanup before submitting

**Owner:** Muse Code for the code steps, the human for git, keys and deploy. **Day:** D8 (Nov 4), after the Opus review fixes from T15 and before submitting. **Read:** AGENTS.md section 4; DATA_AND_LEGAL "Repo hygiene"; DEMO "Submission checklist".

## Goal

The public repo contains only what should be public, the code has no leftovers, and every check is green, so the submitted state is clean and easy for judges to read.

## Steps

### 1. Nothing private or copyrighted is tracked (human)

```bash
git ls-files | grep -iE "\.pdf$|^data/(work|cache|uploads|eval)/|ledger|\.env$"   # must print nothing
uv run pytest -q tests/unit/test_repo_hygiene.py
```

Search for real names from your private note (project names, promoter names, registration numbers):

```bash
git grep -niE "<name 1>|<name 2>|<reg no 1>"   # must print nothing
```

If anything shows up: remove it, commit. If a filing photo or a real name was ever committed, removing it in a new commit doesn't erase it from history. Tell Opus or Claude which file, and decide together whether to rewrite history before the deadline.

### 2. No secrets anywhere, including history (human)

```bash
git log -p | grep -nE "MODEL_API_KEY=.{8,}|BASETEN_API_KEY=.{8,}|FIREWORKS_API_KEY=.{8,}"   # must print nothing
```

If a key ever appears: revoke it on that provider's dashboard right away, create a new one, update `.env` and the Modal secret. Revoking is the only real fix; a key in git history counts as leaked.

### 3. Code leftovers (Muse Code)

```bash
uv run ruff check --select F401,F841,ERA001 src scripts tests   # unused imports, unused variables, commented-out code
git grep -nE "TODO|FIXME|XXX|breakpoint\(|pdb" -- src scripts web/src
git grep -nE "print\(" -- src                                    # library code logs, it doesn't print
git grep -n "PLACEHOLDER" -- src web/src
```

Fix each hit or explain it in PROGRESS.md. Don't refactor working code at this stage: delete only what is clearly unused.

Scripts: keep the `probe_*`, `spike_*`, `bench_*`, `eval_*` and `compare_*` scripts (they show how every number was measured). Each must refuse to run without `STACKCHECK_LIVE=1`; check by running one without it.

### 4. Files and docs (Muse Code drafts, human decides)

- `STUCK.md`: every item marked RESOLVED with one line on the fix, or deleted if empty.
- `PROGRESS.md`: keep it (it's the dated proof of new work). Remove nothing; add a final line "Submitted <date, time IST>".
- `docs/` and `tasks/`: keep them. They show how the project was planned and built, which supports the "new work" rule.
- `docs/kpi_snapshot.md`: regenerate with `uv run python scripts/kpi_report.py` so the committed snapshot matches the README.
- README: every link opens (live URL, video, X post, attribution file); every number matches the snapshot.

### 5. Everything green (Muse Code, then the human repeats it)

```bash
uv sync --locked                      # lock file matches pyproject
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
uv run pytest --cov=stackcheck.rules --cov=stackcheck.vision --cov-branch -q
cd web && npm ci && npm run check && npm run build && cd ..
```

### 6. Mark the submitted state (human)

```bash
git add -A && git commit -m "Submission state"
git tag -a v1.0-submission -m "Meta Global AI Developer Hackathon submission, Nov 4 2026"
git push && git push --tags
```

The tag lets judges see exactly what was submitted, even if you fix a typo later.

### 7. After judging (human, later)

Keep the live app running until the community vote ends (winner announced Nov 16). After that, set `min_containers=0` in `deploy/modal_app.py` and redeploy so the warm container stops costing credits. Revoke or rotate the API keys used during the hackathon if you won't use them again.

## Done when

Steps 1 to 6 all pass with no output from the "must print nothing" commands, and the tag is pushed before you submit on the CV platform.

## How an expert thinks about it

Cleanup on the last day is about removing risk, not polishing. The two things that can hurt a submission are a leaked key and a file you had no right to publish; both are checked first. Everything else (dead code, TODOs) is cheap to fix and makes the repo easier for a judge to trust in the few minutes they spend on it.

## Traps

- Don't run a big refactor or upgrade dependencies on submission day.
- `git rm` doesn't remove a file from history. Revoke secrets; don't rely on deleting them.
- Don't delete the measurement scripts or PROGRESS.md: they're your evidence.
