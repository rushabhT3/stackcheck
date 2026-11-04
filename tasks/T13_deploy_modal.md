# T13: Deploy on Modal

**Owner:** Muse Code writes the file; the human deploys. **Day:** D6. **Read:** AGENTS.md; ARCHITECTURE D13, D14; CONTRACTS C1, C16 (`JobRunner(on_finished=...)`); FACTS "Modal"; SETUP section 7.

## Goal

A public URL that serves the app, runs checks in the background, and keeps data on a Modal Volume, with one warm container during judging.

## Files

Create: `deploy/modal_app.py`, `deploy/README.md`.

## Steps

1. `deploy/modal_app.py`:

```python
import modal

app = modal.App("stackcheck")
volume = modal.Volume.from_name("stackcheck-data", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.13")  # try "3.15" first; see step 3
    .uv_sync()  # installs from pyproject.toml + uv.lock
    .add_local_python_source("stackcheck")
    .add_local_dir("web/dist", remote_path="/app/web/dist")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("stackcheck-keys")],
    volumes={"/data": volume},
    min_containers=1,
    max_containers=1,  # jobs live in one process; never split them across containers
    scaledown_window=900,
    timeout=900,
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def web():
    from stackcheck.api.app import create_app
    from stackcheck.config import Settings
    from stackcheck.pipeline.jobs import JobRunner

    settings = Settings(
        data_dir="/data",
        samples_file="/data/samples.json",
        web_dist_dir="/app/web/dist",
        redact_names=True,
    )
    runner = JobRunner(settings, on_finished=volume.commit)
    return create_app(settings, runner)
```

2. `deploy/README.md`: the commands from SETUP section 7, plus:
   - `modal volume put stackcheck-data data/samples.json /samples.json` to publish the sample list;
   - how to read logs: `modal app logs stackcheck`;
   - how to stop the warm container after judging: set `min_containers=0` and redeploy.
3. First deploy (human): try `python_version="3.15"`; if the image build fails because that version isn't offered, switch to `"3.13"` and record which worked in FACTS.
4. Check on a phone: `/api/health`, start page, one sample report, one new check from 2 URLs.

## Tests first

No unit tests for Modal glue. Add one test that imports `create_app` with `Settings(redact_names=True, web_dist_dir=tmp)` and checks that a report built with a filing named "Sunrise Heights" comes back with `"[redacted]"` in `evidence.quarters[0].filing.project_name` (this verifies the redaction path the deploy relies on).

## Done when (part of G6)

The live URL works from a phone; a new check finishes; after a redeploy the earlier check's report is still there (Volume persisted).

## How an expert thinks about it

One warm container and a Volume is the simplest thing that survives judging week: no cold start when a judge clicks, and reports outlive redeploys. `on_finished=volume.commit` is the one Modal-specific line the app needs, so the rest of the code stays host-agnostic.

## Traps

- `web/dist` must be built (`npm run build`) before `modal deploy`, or the image ships without a frontend.
- Never put keys in `modal_app.py`; only `modal.Secret.from_name`.
- Running checks hold state in memory; more than one container would split jobs. Keep `max_containers=1`. If Modal rejects that parameter name in modal 1.6, check the current `@app.function` reference and record the right name in FACTS.
