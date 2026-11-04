# Deploying StackCheck on Modal

One warm container serves the app; checks run in the background; reports and
uploads persist on a Modal Volume. URLs look like
`https://<workspace>--stackcheck-web.modal.run`.

## Prereqs

- `modal` installed and logged in (`modal setup`).
- `web/dist` built — the image ships whatever is in `web/dist`, so rebuild first.

## Commands

```bash
uv tool install modal  # once; modal stays a uv tool, not a project dependency
modal secret create stackcheck-keys MODEL_API_KEY=... BASETEN_API_KEY=... FIREWORKS_API_KEY=... STACKCHECK_EXTRACT_PROVIDER=baseten WHATSAPP_TOKEN=... WHATSAPP_PHONE_NUMBER_ID=... WHATSAPP_VERIFY_TOKEN=... WHATSAPP_APP_SECRET=...
cd web && npm run build && cd ..
modal volume put stackcheck-data data/samples.json /samples.json
uv run --with modal modal deploy deploy/modal_app.py
```

Deploy through `uv run --with modal`: the bare `modal` shim runs in its own
tool environment where `add_local_python_source("stackcheck")` cannot see the
local package ("has no spec" error). `uv run --with modal` puts the project
environment (with `stackcheck` installed) underneath it.

Note: `modal deploy` on Windows prints `DeprecationWarning`s about
`asyncio.WindowsSelectorEventLoopPolicy` from Modal's own client code. They
are upstream noise, harmless, and don't affect the Linux container.

## First deploy: Python version

`deploy/modal_app.py` pins `python_version="3.13"`. Try `"3.15"` first; if the
image build fails because that version isn't offered, switch back to `"3.13"`.
Record which worked in `docs/FACTS.md`.

## Logs

```bash
modal app logs stackcheck
```

## Seeding the fetch cache (fallback for a blocked portal)

When the portal refuses the server's address, upload the PDFs to the Volume
once and serve checks from the cache. Origin stays "url", so seeded checks use
the open-weights reader and cite the portal link.

```bash
modal volume put stackcheck-data data/projects /seed/projects
uv run --with modal modal run deploy/modal_app.py::seed_cache --manifest-path /data/seed/projects/manifest.csv --base-dir /data/seed/projects
```

Manifest CSV columns: `url, local_path` (local path relative to `--base-dir`).
Each row prints `OK <url>` or `FAIL <url>: <reason>`; only `%PDF` files under
the fetch size cap are stored. A later check on the same URL reuses the bytes
without touching the portal.

## Fetching through Mumbai (when the portal blocks the default region)

Probe both regions first (two requests per region, no cache or breaker):

```bash
uv run --with modal modal run deploy/probe_region.py
```

If Mumbai (`ap-south`) answers 200 and the default region does not, route
the blocked hosts through a Mumbai function: add
`STACKCHECK_FETCH_MUMBAI_HOSTS=rera.bihar.gov.in` (comma list) to the
`stackcheck-keys` secret and redeploy. `fetch_mumbai` does one direct GET per
URL from ap-south with the same client config as `fetch_pdf` and returns the
body (up to 8 MB; larger files fall back to the local connection). Remove the
variable to go back to direct fetching.

## WhatsApp intake

App: create an app on developers.facebook.com, add the WhatsApp product, get
the test number, add the recipient phone as a test recipient, create a
system-user token. Webhook URL is
`https://<workspace>--stackcheck-web.modal.run/webhooks/whatsapp`,
subscribe to `messages`. Put the four `WHATSAPP_*` values into the
`stackcheck-keys` secret and redeploy. The image installs `ffmpeg` for voice
notes. Report links in replies use the `public_base_url` in
`deploy/modal_app.py`; update it if the app URL changes. WhatsApp files are
uploads, so they use the private reader; only salted phone hashes are stored,
and unreferenced uploads are deleted after 7 days.

## After judging

Stop the warm container: set `min_containers=0` in `deploy/modal_app.py` and
redeploy.

## Phone check (G6)

1. Open `/api/health` — expect `{"ok": true}`.
2. Open the start page, then one sample report.
3. Start one new check from 2 filing URLs; it must finish.
4. Redeploy, then reopen the earlier check's report — it must still be there
   (Volume persisted).
