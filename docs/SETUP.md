# SETUP: from a clean laptop to a running StackCheck

Commands are given for macOS/Linux and Windows PowerShell where they differ.

## 1. Accounts and keys (human, once)

| What | Where | Used for |
|---|---|---|
| Meta Model API key | dev.meta.ai, API keys page | `MODEL_API_KEY`: Muse Spark, SAM 3.1 |
| Meta Billing | dev.meta.ai, Billing page | Confirm the $150 credit; set spend notifications at $50, $100, $140 |
| Baseten API key | Baseten dashboard | `BASETEN_API_KEY` |
| Fireworks API key | app.fireworks.ai, API keys | `FIREWORKS_API_KEY` |
| Modal | `modal setup` (below) | Deploy |
| GitHub | new **public** repo `stackcheck` | Code |

Never paste a key into a Muse Code chat. Contributor mode can use prompts for training, and keys don't belong in prompts anyway.

## 2. Tools

```bash
# uv (Python manager)
curl -LsSf https://astral.sh/uv/install.sh | sh                 # macOS/Linux
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows

uv python install 3.15

# Node 22.12 or newer (check with: node --version). Install from nodejs.org if older.

# Muse Code
curl -fsSL https://dev.meta.ai/install.sh | sh                  # macOS/Linux
irm https://dev.meta.ai/install.ps1 | iex                       # Windows PowerShell
muse --version

# Modal CLI (as a uv tool, not a project dependency)
uv tool install modal
modal setup                                                     # browser sign-in
```

## 3. Repo

```bash
git clone https://github.com/<you>/stackcheck.git
cd stackcheck
# copy this kit's files into the repo root: AGENTS.md, A1_PROMPT.md, docs/, tasks/
git add . && git commit -m "Build kit: spec, contracts, task cards (Oct 28, 2026)"
git push
```

Task T01 creates `pyproject.toml`, `.env.example` and the package skeleton. After T01:

```bash
cp .env.example .env            # Windows: copy .env.example .env
# fill MODEL_API_KEY, BASETEN_API_KEY, FIREWORKS_API_KEY in .env
uv sync
uv run pytest -q
```

## 4. Running Muse Code on a task

From the repo root:

```bash
muse --model muse-spark-1.3-contributor
```

Don't run `muse init`: it seeds its own `AGENTS.md`, and this repo already has the right one.

On the first run, sign in with `/login`. Pick the API-key option and enter your Model API key in that sign-in prompt (not in the chat), so usage is billed against your hackathon credit. Whether the credit covers Muse Code pay-as-you-go isn't confirmed (FACTS, UNVERIFIED), so check the Billing page the next morning. If it isn't covered, the $5 Everyday subscription is the cheap fallback.

First message of every session:

```text
Read AGENTS.md and PROGRESS.md, then do tasks/T0X_<name>.md. Follow the loop in AGENTS.md section 3 and stop when the card is done.
```

One task card per session. Start a fresh session for the next card: shorter context is cheaper and the agent makes fewer mistakes.

After the agent reports:

```bash
git diff                         # read it
uv run pytest -q                 # confirm green yourself
git add -A && git commit -m "T0X: <title>"
git push
```

Keep the default approval mode (`on-request`). Don't use `--yolo` on your laptop.

## 5. Live probes and live tests (human runs these)

```bash
# macOS/Linux
STACKCHECK_LIVE=1 uv run python scripts/smoke_meta.py
# Windows PowerShell
$env:STACKCHECK_LIVE="1"; uv run python scripts/smoke_meta.py
```

Paste the output (no keys appear in it) back to Muse Code if the card asks for it.

## 6. Run the app locally

```bash
uv run uvicorn stackcheck.api.app:create_app --factory --reload --port 8000
cd web && npm install && npm run dev        # http://localhost:5173, proxies /api and /media to :8000
```

Production build of the frontend: `cd web && npm run build` (FastAPI then serves `web/dist` at http://localhost:8000).

## 7. Deploy (task T13)

```bash
modal secret create stackcheck-keys MODEL_API_KEY=... BASETEN_API_KEY=... FIREWORKS_API_KEY=...
cd web && npm run build && cd ..
modal deploy deploy/modal_app.py
```

The URL looks like `https://<workspace>--stackcheck-web.modal.run`.
