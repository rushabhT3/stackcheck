# T10: HTTP API

**Owner:** Muse Code. **Day:** D4. **Read:** AGENTS.md; CONTRACTS C1, C9 (validation), C16 (`JobRunner`), C17.

## Goal

A small FastAPI app that accepts uploads and check requests, reports status, serves reports and overlay images safely, and serves the built frontend.

## Files

Create: `src/stackcheck/api/app.py`, `tests/unit/test_api.py`.

## Steps

1. `create_app(settings: Settings | None = None, runner: JobRunner | None = None) -> FastAPI`. Uses a lifespan handler to create the runner (and run restart recovery) when none is passed.
2. Routes exactly as C17. Pydantic request models for `POST /api/checks`: `project_label` 1 to 80 characters, `sources` 1 to 24 items, each with exactly one of `url` or `upload_id` (validator).
3. URL validation before submitting: https and allowed host (reuse the helper from `ingest/sources.py`), else 400 `host_not_allowed`.
4. Uploads: content type `application/pdf` or name ending `.pdf`, size ≤ `fetch_max_bytes` (read in chunks and stop early), `%PDF` magic; saved under `data_dir/uploads/`; `upload_id` = `doc_id`.
5. Error handler that returns `{"error": {"code", "message"}}` for `StackCheckError`, `RequestValidationError` (422), `QueueFull` (429) and unknown errors (500 with a generic message; details only in logs).
6. `/media`: validate both path parts with the regexes in C17 before touching the filesystem; resolve the path and confirm it is inside the media folder.
7. Static: if `settings.web_dist_dir` exists, mount `StaticFiles` at `/assets` from `web_dist_dir / "assets"` (where Vite puts bundles), then add a final catch-all `GET /{path:path}` route that returns the file at `web_dist_dir / path` when it exists inside that folder, and `index.html` otherwise. Paths starting with `api/` or `media/` never reach it (their routes are registered first). If the folder doesn't exist, `/` returns a one-line text "Frontend not built".

## Tests first

`test_api.py` with `TestClient` and a fake runner:
- `GET /api/health` → 200, `ok` true;
- upload a small synthetic PDF → 200 with `upload_id`; upload a PNG → 400; upload over the cap → 413;
- `POST /api/checks` with a non-allowed host → 400 `host_not_allowed`; with 25 sources → 422; with both `url` and `upload_id` in one item → 422; valid → 202 with `check_id`;
- `GET /api/checks/chk_0000000000` unknown → 404;
- report before done → 404; after the fake runner marks done → 200 with a `Report`;
- `/media/chk_0000000000/../../.env` → 404 (never reads outside); a name with a slash → 404; a valid existing PNG → 200 `image/png`;
- `/api/samples` with no file → `[]`; with a file → its contents;
- static fallback: create a temp `web_dist_dir` with `index.html`, `GET /report/abc` → `index.html`.

## Done when

Tests green; human starts the server, opens `/api/health`, and runs one real check through `curl` or the docs page (`/docs`).

## How an expert thinks about it

The API is thin on purpose: validation and safety at the edge, everything else in the pipeline. The two places public apps get hurt are file paths and fetched URLs; both are checked here before any work starts, with tests that try the obvious attacks.

## Traps

- FastAPI's `StaticFiles` mount at `/` would swallow `/api`: register API routes first and use a catch-all route for the fallback instead of mounting at root.
- Don't block the event loop reading big uploads: use `await file.read(chunk)` in a loop.
