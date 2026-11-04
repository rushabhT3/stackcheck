# T11: Frontend (Svelte 5)

**Owner:** Muse Code. **Day:** D5. **Read:** AGENTS.md (section 5, Svelte rules); CONTRACTS C3 (shapes), C17, C18; SPEC "The demo moment"; DEMO video rows 0:35 to 2:10.

## Goal

Three views that make the report readable in 20 seconds: what's wrong, the proof, what couldn't be checked. Works at phone width, light and dark, keyboard accessible.

## Files

Create `web/` with Vite + Svelte 5 + TypeScript:

```
web/
├── package.json  vite.config.ts  tsconfig.json  svelte.config.js  index.html
└── src/
    ├── main.ts  App.svelte  app.css
    ├── lib/ api.ts  types.ts  format.ts  router.svelte.ts
    └── components/ StartView.svelte  ProgressView.svelte  ReportView.svelte
        VerdictBanner.svelte  FindingCard.svelte  PhotoEvidence.svelte  QuarterTimeline.svelte
        MoneyChart.svelte  CantVerifyList.svelte  AgentTrace.svelte  CostBadge.svelte  Disclaimer.svelte  StatusTag.svelte
```

## Steps

1. Scaffold: `npm create vite@latest web -- --template svelte-ts`, then set exact versions in `package.json`: `svelte 5.57.3`, `vite 8.3.5`, `@sveltejs/vite-plugin-svelte 7.3.1`, `typescript 6.0.3`, `svelte-check 4.7.7`, `@tsconfig/svelte` as the template sets it. Scripts: `dev`, `build`, `check` (`svelte-check --tsconfig ./tsconfig.json`). Delete the template's demo files.
2. `vite.config.ts`: dev proxy `/api` and `/media` to `http://localhost:8000`; build output `dist`.
3. `types.ts`: TypeScript types mirroring C3 (`Report`, `Finding`, `Evidence`, `CheckStatus`, ...). Dates and Decimals are strings.
4. `api.ts`: `async` functions for every C17 endpoint; throw an `ApiError` with the server's `code` and `message`.
5. `router.svelte.ts`: a `$state` holding the current hash route, updated on `hashchange`. Routes: `#/`, `#/check/<id>`, `#/report/<id>`.
6. `StartView`: project label input; a textarea for QPR URLs (one per line); a PDF upload (multiple files, each uploaded first, then included as `upload_id`); a list of sample projects from `/api/samples` as cards linking to their reports; the disclaimer. Submit → `POST /api/checks` → go to `#/check/<id>`.
7. `ProgressView`: polls status every 1.5 s with `$effect` and a cleanup that clears the timer; shows the 7 steps with state icons and notes; on `done` goes to the report; on `failed` shows the error and a "Start again" link.
8. `ReportView` layout, top to bottom:
   - `VerdictBanner`: headline, counts by severity, `CostBadge`;
   - FLAG findings as `FindingCard`s, high first; each card: `StatusTag`, title, explanation, evidence list (quotes with "page N", values, computations), photo thumbnails opening `PhotoEvidence` (full photo, overlay on/off toggle, rows legend);
   - `QuarterTimeline`: SVG, x = quarters, claimed slabs as a line with dots, visible floor range as vertical bars (missing data shown as a hollow marker with "no data");
   - `MoneyChart`: SVG, spent share vs progress share per quarter, two labelled lines;
   - `CantVerifyList`: each CANT_VERIFY with its reason;
   - OK findings collapsed under "Checks that passed (n)";
   - `AgentTrace`: collapsible list of agent steps (action, argument, rationale, program result);
   - summary (English; Hindi toggle only if `summary_hi` exists);
   - `Disclaimer`.
9. `StatusTag`: icon + word + colour for FLAG (`--flag`), OK (`--ok`), CANT_VERIFY (`--unsure`, label "Can't verify"). Never colour alone.
10. `app.css`: tokens from C18; base layout max width 960 px, 16 px side padding, cards with `--radius` and `--border`; focus outlines visible; `prefers-reduced-motion` respected.
11. Images: `<img loading="lazy" alt="...">` with alt text from the evidence (e.g. "Elevation photo, quarter ending 31 Mar 2025, 3 rows marked").

## Tests first

No browser test framework is approved, so the frontend is kept free of logic that needs one. Instead:
- `npm run check` must report 0 errors and 0 warnings;
- `npm run build` must succeed;
- `format.ts` holds only three small pure functions (`formatInr`, `formatDate`, `statusLabel`). All real formatting of titles and explanations happens in the tested Python rules (T08); the UI only displays it.

Manual checklist (the human runs it, the agent writes it into PROGRESS.md for the human to tick):
- [ ] Start → submit 2 sample URLs → progress → report, no console errors.
- [ ] Report readable at 390 px width, no horizontal scroll.
- [ ] Dark mode: every text readable.
- [ ] Tab through the report: every control reachable, focus visible.
- [ ] Overlay toggle works; alt text present.
- [ ] Failed check shows the error, not a spinner.

## Done when (G5)

`npm run check` clean, `npm run build` succeeds, FastAPI serves `web/dist`, and the manual checklist passes on a real report.

## How an expert thinks about it

A judge looks for five seconds before deciding whether to look longer. So the order is: verdict, the strongest finding with its photo, then everything else. Every claim on screen links to its proof. The charts are small hand-made SVGs because two lines and some bars don't need a library, and fewer dependencies means fewer things for the agent to get wrong.

## Traps

- Svelte 5 only: `let { report } = $props();`, `let open = $state(false);`, `onclick={...}`. No `export let`, no `$:`, no `on:click`.
- `$effect` that sets an interval must return a cleanup function.
- Don't compute findings in the UI. Display what the API returns.
- Don't hard-code sample data in components.
