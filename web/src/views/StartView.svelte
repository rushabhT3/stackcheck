<script lang="ts">
  import { untrack } from "svelte";
  import { createCheck, getSamples, getStatus, uploadPdf, uploadPhoto, uploadVideo, userMessage } from "../lib/api";
  import { parsePdfUrls } from "../lib/urls";
  import { navigate } from "../lib/router.svelte";
  import type { CheckSource, Sample } from "../lib/types";
  import {
    addRecentCheck,
    clearRecentChecks,
    loadRecentChecks,
    type RecentCheck,
  } from "../lib/recent";
  import { formatDate } from "../lib/format";

  let projectLabel = $state("");
  let urls = $state("");
  let floorsAboveGround: number | "" | null = $state("");
  let files = $state<FileList | undefined>(undefined);
  let videoFiles = $state<FileList | undefined>(undefined);
  let photoFiles = $state<FileList | undefined>(undefined);
  let videoBlock = $state("");
  let videoSameProject = $state(false);
  let videoReset = $state(0);
  let photoReset = $state(0);

  function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  function removeVideo(): void {
    videoFiles = undefined;
    videoSameProject = false;
    videoReset += 1;
  }

  function removePhotos(): void {
    photoFiles = undefined;
    photoReset += 1;
  }
  let samples = $state<Sample[]>([]);
  let recents = $state<RecentCheck[]>([]);
  let recentState = $state<Record<string, string>>({});
  let recentPage = $state(0);

  const RECENT_PAGE_SIZE = 5;
  let recentPageCount = $derived(Math.max(1, Math.ceil(recents.length / RECENT_PAGE_SIZE)));
  let recentVisible = $derived(
    recents.slice(recentPage * RECENT_PAGE_SIZE, recentPage * RECENT_PAGE_SIZE + RECENT_PAGE_SIZE),
  );
  let busy = $state(false);
  let error = $state<string | null>(null);

  function stateWord(state: string): string {
    return state === "done" ? "Done" : state === "failed" ? "Failed" : "Running";
  }

  function stateTone(word: string): string {
    return word === "Done" ? "ok" : word === "Failed" ? "flag" : "unsure";
  }

  $effect(() => {
    document.title = "StackCheck";
  });

  $effect(() => {
    let alive = true;
    let timer: number | undefined = undefined;
    recents = loadRecentChecks();
    getSamples()
      .then((s) => {
        if (alive) samples = s;
      })
      .catch(() => {
        if (alive) samples = [];
      });
    async function refresh(): Promise<void> {
      const list = untrack(() => recents);
      const updates: Record<string, string> = {};
      await Promise.all(
        list.map((r) =>
          getStatus(r.check_id).then(
            (st) => {
              updates[r.check_id] = stateWord(st.state);
            },
            () => {
              // A failed fetch says nothing about the check: keep the last word.
            },
          ),
        ),
      );
      if (!alive) return;
      recentState = { ...untrack(() => recentState), ...updates };
      const stillRunning = list.some(
        (r) => (recentState[r.check_id] ?? "Running") === "Running",
      );
      if (!stillRunning && timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    }
    void refresh();
    timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      alive = false;
      if (timer !== undefined) window.clearInterval(timer);
    };
  });

  $effect(() => {
    const maxPage = Math.max(0, Math.ceil(recents.length / RECENT_PAGE_SIZE) - 1);
    if (recentPage > maxPage) recentPage = maxPage;
  });

  function clearRecents(): void {
    clearRecentChecks();
    recents = [];
    recentState = {};
    recentPage = 0;
  }

  async function submit(): Promise<void> {
    error = null;
    const urlList = parsePdfUrls(urls);
    const hasFiles = files !== undefined && files.length > 0;
    const hasPhotos = photoFiles !== undefined && photoFiles.length > 0;
    const hasVideo = videoFiles !== undefined && videoFiles.length > 0;
    if (projectLabel.trim() === "" || (urlList.length === 0 && !hasFiles && !hasPhotos && !hasVideo)) {
      error = "Enter a project name and at least one filing, photo or video.";
      return;
    }
    let floors: number | undefined = undefined;
    const raw = floorsAboveGround;
    if (raw !== "" && raw !== null) {
      if (!Number.isInteger(raw) || raw < 1) {
        error = "Floors above ground must be a whole number of 1 or more.";
        return;
      }
      floors = raw;
    }
    if (hasPhotos && (photoFiles as FileList).length > 5) {
      error = "You can add up to 5 site photos.";
      return;
    }
    if (hasPhotos) {
      for (const file of photoFiles as FileList) {
        if (!/\.(jpe?g|png|webp)$/i.test(file.name)) {
          error = "Site photos must be JPEG, PNG or WebP images.";
          return;
        }
      }
    }
    busy = true;
    try {
      const sources: CheckSource[] = urlList.map((url) => ({ url }));
      if (hasFiles) {
        for (const file of files as FileList) {
          const up = await uploadPdf(file);
          sources.push({ upload_id: up.upload_id });
        }
      }
      const photoIds: string[] = [];
      if (hasPhotos) {
        for (const file of photoFiles as FileList) {
          const up = await uploadPhoto(file);
          photoIds.push(up.upload_id);
        }
      }
      let video = undefined;
      if (hasVideo) {
        if (sources.length > 0 && !videoSameProject) {
          error = "Confirm the video shows the same project and block as the filing.";
          return;
        }
        const up = await uploadVideo((videoFiles as FileList)[0]);
        video = {
          video_upload_id: up.upload_id,
          video_block_label: videoBlock.trim() === "" ? "MAIN" : videoBlock.trim(),
          video_same_project: videoSameProject,
        };
      }
      // No-photo path keeps the original call, so its request body is unchanged.
      const created =
        photoIds.length === 0
          ? await createCheck(projectLabel.trim(), sources, video, floors)
          : await createCheck(projectLabel.trim(), sources, video, floors, photoIds);
      recents = addRecentCheck({
        check_id: created.check_id,
        project_label: projectLabel.trim(),
        created_at: new Date().toISOString(),
      });
      navigate(`#/check/${created.check_id}`);
    } catch (e) {
      error = userMessage(e);
    } finally {
      busy = false;
    }
  }

  /* ---- Presentational additions (visual takeover only; no behavior change) ---- */

  const RULES: string[] = [
    "Slabs claimed vs floors in photos",
    "Reused photos across quarters",
    "Money spent vs work done",
    "Activity percentages that go backwards",
    "Photo dates vs reporting quarter",
    "Completion date vs pace",
    "Currency unit slips (lakh vs crore)",
    "Video floor counts",
  ];

  const HOW_STEPS: { n: string; t: string; d: string }[] = [
    { n: "01", t: "Bring the filings", d: "Public RERA PDF links, or upload your own — one per quarter, oldest first." },
    { n: "02", t: "StackCheck reads them", d: "Claims, construction photos and money are extracted, then cross-checked against each other." },
    { n: "03", t: "Read the evidence", d: "Every flag cites its page, photo or computation. Gaps and questions, never accusations." },
  ];

  const CHECK_POINTS: string[] = [
    "Every flag cites a page, photo or computation",
    "Unclear evidence says “can't verify”, never a guess",
    "Model spend shown on every report",
  ];

  function sampleFlagged(s: Sample): boolean {
    return !/^no /i.test(s.headline);
  }

  function scrollToCheck(): void {
    document.getElementById("check")?.scrollIntoView({ behavior: "smooth" });
  }

  /** Scroll-into-view entrance (replaces framer-motion Reveal; CSS + IntersectionObserver only). */
  function reveal(node: HTMLElement, delay: number = 0): { destroy(): void } {
    node.style.setProperty("--sc-delay", `${delay}ms`);
    if (
      typeof IntersectionObserver === "undefined" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      node.classList.add("is-visible");
      return { destroy(): void {} };
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            node.classList.add("is-visible");
            io.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -60px 0px" },
    );
    io.observe(node);
    return {
      destroy(): void {
        io.disconnect();
      },
    };
  }
</script>

{#snippet building(floors: number, total: number = 14, claimed: number | null = null, tone: "dark" | "light" = "light")}
  {@const count = Math.max(total, floors, claimed ?? 0, 1)}
  {@const groundY = 262}
  {@const fh = Math.min(20, (groundY - 34) / count)}
  {@const x0 = 62}
  {@const w = 96}
  {@const c =
    tone === "dark"
      ? {
          ink: "#eaf6e2",
          dim: "rgb(215 255 68 / 0.35)",
          fill: "rgb(215 255 68 / 0.10)",
          win: "#d7ff44",
          mark: "#d7ff44",
          flag: "#ff6b4d",
          ground: "rgb(255 255 255 / 0.5)",
        }
      : {
          ink: "#0b1510",
          dim: "rgb(11 21 16 / 0.35)",
          fill: "rgb(215 255 68 / 0.28)",
          win: "#0e3a29",
          mark: "#0b1510",
          flag: "#ff4a2b",
          ground: "#0b1510",
        }}
  {@const claimY = claimed !== null ? groundY - claimed * fh : null}
  <svg viewBox="0 0 220 290" class="sc-building" role="img" aria-label={`Building sketch with ${floors} floors visible`}>
    <line x1="14" y1={groundY} x2="206" y2={groundY} stroke={c.ground} stroke-width="2" />
    {#each Array.from({ length: 10 }, (_, i) => i) as i (i)}
      <line x1={20 + i * 20} y1={groundY} x2={12 + i * 20} y2={groundY + 8} stroke={c.dim} stroke-width="1" />
    {/each}
    <g stroke={c.dim} stroke-width="1.5" fill="none">
      <line x1="182" y1={groundY} x2="182" y2="14" />
      <line x1="176" y1={groundY} x2="176" y2="14" />
      <path d="M176 262 L182 232 L176 202 L182 172 L176 142 L182 112 L176 82 L182 52" />
      <line x1="100" y1="14" x2="206" y2="14" stroke-width="2.5" />
      <line x1="182" y1="14" x2="140" y2="2" />
      <line x1="104" y1="14" x2="104" y2="30" />
    </g>
    {#each Array.from({ length: count }, (_, i) => i) as i (i)}
      {@const y = groundY - (i + 1) * fh}
      {@const built = i < floors}
      <g>
        <rect
          x={x0}
          y={y}
          width={w}
          height={fh}
          fill={built ? c.fill : "none"}
          stroke={built ? c.ink : c.dim}
          stroke-width={built ? 1.5 : 1}
          stroke-dasharray={built ? undefined : "3 4"}
        />
        {#if built}
          {#each [0, 1, 2, 3] as k (k)}
            <rect x={x0 + 9 + k * 22} y={y + fh * 0.28} width="12" height={fh * 0.44} fill={c.win} opacity="0.85" />
          {/each}
          <line x1={x0 - 10} y1={y + fh / 2} x2={x0 - 2} y2={y + fh / 2} stroke={c.mark} stroke-width="2" />
          <text x={x0 - 14} y={y + fh / 2 + 3.5} text-anchor="end" font-size="9.5" font-family="var(--font-mono)" font-weight="700" fill={c.mark}>
            {i + 1}
          </text>
        {/if}
      </g>
    {/each}
    {#if claimY !== null && claimed !== null}
      <g>
        <line x1={x0 - 6} y1={claimY} x2={x0 + w + 14} y2={claimY} stroke={c.flag} stroke-width="2" stroke-dasharray="6 4" />
        <text x={x0 + w + 14} y={claimY - 5} text-anchor="end" font-size="10" font-family="var(--font-mono)" font-weight="700" fill={c.flag}>
          claimed {claimed}
        </text>
      </g>
    {/if}
  </svg>
{/snippet}

<section class="sc-hero on-dark noise" aria-labelledby="start-heading">
  <div class="sc-fill grid-bg" aria-hidden="true"></div>
  <div class="sc-glow" aria-hidden="true"></div>
  <div class="sc-hero-grid">
    <div>
      <div class="sc-enter" style="--sc-delay: 0ms;">
        <span class="eyebrow">For home-buyers · Researchers · RERA reviewers</span>
      </div>
      <h1 id="start-heading" class="display sc-title">
        <span class="sc-enter sc-line" style="--sc-delay: 150ms;">The builder says</span>
        <span class="sc-enter sc-line" style="--sc-delay: 300ms;"><em class="sc-lime">twelve floors.</em></span>
        <span class="sc-enter sc-line sc-dim" style="--sc-delay: 450ms;">The photo says <em>six.</em></span>
      </h1>
      <p class="sc-enter sc-lede" style="--sc-delay: 700ms;">
        StackCheck reads a builder's RERA quarterly progress reports and compares them with the builder's
        <strong>own photos and money</strong>. Every finding carries its page, photo or computation.
      </p>
      <div class="sc-enter sc-cta" style="--sc-delay: 850ms;">
        <button type="button" class="btn sc-hero-btn" onclick={scrollToCheck}>
          Run a check
          <span aria-hidden="true">→</span>
        </button>
      </div>
    </div>
    <div class="sc-enter sc-fig-wrap" style="--sc-delay: 300ms;" aria-hidden="true">
      <div class="sc-fig">
        <div class="sc-fig-head">
          <span>Fig. 1 — Dec filing</span>
          <span class="sc-live"><span class="sc-live-dot"></span> Live compare</span>
        </div>
        {@render building(7, 14, 12, "dark")}
        <div class="sc-stat-grid">
          <div class="sc-stat sc-stat-flag">
            <div class="sc-stat-cap">Filing claims</div>
            <div class="display sc-stat-num">12 <span class="sc-stat-unit">slabs</span></div>
          </div>
          <div class="sc-stat sc-stat-lime">
            <div class="sc-stat-cap">Photo shows</div>
            <div class="display sc-stat-num">6–7 <span class="sc-stat-unit">floors</span></div>
          </div>
        </div>
      </div>
      <div class="sc-badge-float">✕ 5 floors unexplained</div>
    </div>
  </div>
</section>

<div class="sc-marquee" aria-hidden="true">
  <div class="sc-marquee-track">
    {#each [0, 1, 2, 3] as rep (rep)}
      {#each RULES as r (rep + r)}
        <span class="sc-rule">{r}<span class="sc-diamond">◆</span></span>
      {/each}
    {/each}
  </div>
</div>

<section id="how" class="sc-how" aria-labelledby="how-heading">
  <div class="sc-reveal" use:reveal={0}>
    <span class="eyebrow">How it works</span>
    <h2 id="how-heading" class="display sc-section-title">Three steps from a PDF to <em>proof you can point at.</em></h2>
  </div>
  <div class="sc-steps">
    {#each HOW_STEPS as s, i (s.n)}
      <div class="sc-reveal sc-step" use:reveal={i * 120}>
        <span class="display sc-step-n">{s.n}</span>
        <h3 class="display sc-step-t">{s.t}</h3>
        <p class="sc-step-d">{s.d}</p>
      </div>
    {/each}
  </div>
</section>

<section id="check" class="sc-check" aria-labelledby="check-heading">
  <div class="sc-fill grid-bg-light" aria-hidden="true"></div>
  <div class="sc-check-grid">
    <div class="sc-reveal sc-check-intro" use:reveal={0}>
      <span class="eyebrow">Run a check</span>
      <h2 id="check-heading" class="display sc-check-title">Paste the links. <em>We'll find the gaps.</em></h2>
      <p class="sc-check-lede">
        Public PDF links or your own uploads, one per quarter, oldest first. Add a site video and we'll count floors in
        that too.
      </p>
      <ul class="sc-points">
        {#each CHECK_POINTS as t (t)}
          <li><span class="sc-tick" aria-hidden="true">✓</span>{t}</li>
        {/each}
      </ul>
    </div>
    <div class="sc-reveal" use:reveal={100}>
      <form
        class="sc-form stamp-shadow"
        novalidate
        onsubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div class="sc-form-head">
          <span class="sc-pill">New check</span>
          <span class="sc-eta">≈ 2 minutes</span>
        </div>

        <label for="project-name" class="sc-label">01 · Project name</label>
        <input
          id="project-name"
          class="field"
          type="text"
          bind:value={projectLabel}
          placeholder="Project A (Patna)"
          autocomplete="off"
          maxlength="80"
        />
        <span class="sc-hint">A short label. It is saved with the report on our server.</span>

        <label for="pdf-urls" class="sc-label sc-label-gap">PUBLIC PDF URLS</label>
        <textarea
          id="pdf-urls"
          class="field sc-urls"
          bind:value={urls}
          rows="4"
          placeholder="https://…/QPR-2024-Q4.pdf"
          spellcheck="false"
        ></textarea>
        <span class="sc-hint">One link per line, or separated by commas.</span>

        <span id="pdf-label" class="sc-sr">Upload PDFs, one per quarter, oldest first</span>
        <div class="sc-drop">
          <span class="sc-drop-icon" aria-hidden="true">⇪</span>
          <strong>Or drag &amp; drop PDFs here</strong>
          <span class="sc-drop-hint">PDFs only · one per quarter · oldest first</span>
          <input
            id="pdf-files"
            class="sc-drop-input"
            type="file"
            accept="application/pdf,.pdf"
            multiple
            aria-labelledby="pdf-label"
            onchange={(e) => (files = e.currentTarget.files ?? undefined)}
          />
        </div>
        {#if files !== undefined && files.length > 0}
          <ul class="sc-chips">
            {#each Array.from(files) as f (f.name + f.size)}
              <li class="sc-chip">{f.name} · {formatBytes(f.size)}</li>
            {/each}
          </ul>
        {/if}
        <span class="sc-hint">Filings from other state RERAs can be uploaded too. Rules that need data a filing doesn't carry show “can't verify”.</span>

        <span id="video-label" class="sc-label sc-label-gap">03 · Site video <span class="sc-opt">(optional, at most one)</span></span>
        <div class="sc-drop">
          <span class="sc-drop-icon" aria-hidden="true">▶</span>
          <strong>Drop a site video here</strong>
          <span class="sc-drop-hint">MP4, MOV or WebM · up to 60 s and 50 MB</span>
          {#key videoReset}
            <input
              id="video-file"
              class="sc-drop-input"
              type="file"
              accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
              aria-labelledby="video-label"
              onchange={(e) => (videoFiles = e.currentTarget.files ?? undefined)}
            />
          {/key}
        </div>
        {#if videoFiles !== undefined && videoFiles.length > 0}
          <p class="sc-video-pick">
            <span class="sc-chip">{videoFiles[0].name} · {formatBytes(videoFiles[0].size)}</span>
            <button type="button" class="sc-link-btn" onclick={removeVideo}>Remove</button>
          </p>
        {/if}
        <span class="sc-hint">A walkthrough of the site, 60 seconds or shorter. We sample stills and count floors.</span>

        {#if videoFiles !== undefined && videoFiles.length > 0}
          <div class="sc-videobox">
            <label for="video-block" class="sc-label">Block shown in the video</label>
            <input
              id="video-block"
              class="field"
              type="text"
              bind:value={videoBlock}
              placeholder="MAIN"
              autocomplete="off"
              maxlength="80"
            />
            <label class="check-row" for="video-same">
              <input id="video-same" type="checkbox" bind:checked={videoSameProject} />
              <span>This video shows the same project and block as the filing.</span>
            </label>
          </div>
        {/if}

        <span id="photo-label" class="sc-label sc-label-gap">04 · Site photos <span class="sc-opt">(optional, up to 5)</span></span>
        <div class="sc-drop">
          <span class="sc-drop-icon" aria-hidden="true">▦</span>
          <strong>Drop site photos here</strong>
          <span class="sc-drop-hint">JPEG, PNG or WebP · up to 5</span>
          {#key photoReset}
            <input
              id="photo-files"
              class="sc-drop-input"
              type="file"
              accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
              multiple
              aria-labelledby="photo-label"
              onchange={(e) => (photoFiles = e.currentTarget.files ?? undefined)}
            />
          {/key}
        </div>
        {#if photoFiles !== undefined && photoFiles.length > 0}
          <ul class="sc-chips">
            {#each Array.from(photoFiles) as f (f.name + f.size)}
              <li class="sc-chip">{f.name} · {formatBytes(f.size)}</li>
            {/each}
          </ul>
          <p class="sc-video-pick">
            <button type="button" class="sc-link-btn" onclick={removePhotos}>Remove</button>
          </p>
        {/if}
        <span class="sc-hint">Photos of the site from your phone. We count the floors in each one.</span>

        <label for="floors-above-ground" class="sc-label sc-label-gap">
          05 · Floors above ground <span class="sc-opt">(optional · for example 5 for G+4)</span>
        </label>
        <input
          id="floors-above-ground"
          class="field"
          type="number"
          bind:value={floorsAboveGround}
          placeholder="5"
          autocomplete="off"
          min="1"
          step="1"
        />
        <span class="sc-hint">One number for the whole check. Used only when a filing doesn't say the floors.</span>

        {#if error}
          <p class="sc-error" role="alert">{error}</p>
        {/if}

        <div class="sc-submit">
          <button type="submit" class="btn sc-submit-btn" disabled={busy}>
            {busy ? "Starting…" : "Run check"}
            {#if !busy}<span aria-hidden="true">→</span>{/if}
          </button>
          <span class="sc-no-acct">No account. No card. Evidence in, questions out.</span>
        </div>
      </form>
    </div>
  </div>
</section>

{#if recents.length > 0}
  <section class="sc-recents" aria-labelledby="recent-heading">
    <div class="sc-reveal" use:reveal={0}>
      <h2 id="recent-heading" class="display sc-recent-title">Your recent checks on this device</h2>
      <ul class="sc-recent-list">
        {#each recentVisible as r (r.check_id)}
          {@const st = recentState[r.check_id] ?? "Running"}
          <li>
            <a
              class="sc-recent-link"
              href={st === "Done" ? `#/report/${r.check_id}` : `#/check/${r.check_id}`}
            >
              <span>
                <strong class="display sc-recent-name">{r.project_label}</strong>
                <span class="sc-recent-date">{formatDate(r.created_at.slice(0, 10))}</span>
              </span>
              <span class="sc-tone sc-tone-{stateTone(st)}">{st}</span>
            </a>
          </li>
        {/each}
      </ul>
      <div class="sc-recent-pager">
        <button
          type="button"
          class="sc-link-btn"
          disabled={recentPage === 0}
          onclick={() => (recentPage = Math.max(0, recentPage - 1))}
        >
          Previous
        </button>
        <span class="sc-recent-page" aria-live="polite">Page {recentPage + 1} of {recentPageCount}</span>
        <button
          type="button"
          class="sc-link-btn"
          disabled={recentPage >= recentPageCount - 1}
          onclick={() => (recentPage = Math.min(recentPageCount - 1, recentPage + 1))}
        >
          Next
        </button>
      </div>
      <button type="button" class="sc-link-btn sc-clear" onclick={clearRecents}>Clear list</button>
      <p class="sc-recent-note">Saved in this browser only. Report links work on any device.</p>
    </div>
  </section>
{/if}

{#if samples.length > 0}
  <section id="samples" class="sc-samples" aria-labelledby="samples-heading">
    <div class="sc-reveal" use:reveal={0}>
      <span class="eyebrow">Evidence, not promises</span>
      <h2 id="samples-heading" class="display sc-section-title" aria-label="Try a finished sample">Try a <em>finished</em> sample.</h2>
    </div>
    <div class="sc-sample-grid">
      {#each samples as s, i (s.check_id)}
        {@const flagged = sampleFlagged(s)}
        <div class="sc-reveal sc-sample-cell" use:reveal={i * 120}>
          <a
            href={`#/report/${s.check_id}`}
            class="sc-sample-card {flagged ? 'sc-flagged' : 'sc-clean'}"
          >
            <div class="sc-sample-art {flagged ? 'grid-bg' : 'grid-bg-light'}">
              {@render building(flagged ? 7 : 11, 14, flagged ? 12 : null, flagged ? "dark" : "light")}
              <span class="sc-sample-badge {flagged ? 'sc-badge-flag' : 'sc-badge-ok'}">
                {flagged ? "✕ flags raised" : "✓ lines up"}
              </span>
            </div>
            <div class="sc-sample-body">
              {#if s.constructed}
                <span class="eyebrow sc-constructed">Constructed example</span>
              {/if}
              <strong class="display sc-sample-name">{s.project_label}</strong>
              <p class="sc-sample-head">{s.headline}</p>
              <span class="sc-open">Open report <span class="sc-arrow" aria-hidden="true">→</span></span>
            </div>
          </a>
        </div>
      {/each}
    </div>
  </section>
{/if}

<p class="sc-disclaimer">
  StackCheck compares claims, photos and money across quarters. A gap is a question to ask the builder or RERA, not proof
  of wrongdoing.
</p>

<style>
  .sc-sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  .sc-fill {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  /* ---- Hero (ref StartView.tsx:34-146, contained panel: views render inside .wrap) ---- */
  .sc-hero {
    position: relative;
    overflow: hidden;
    background: var(--color-forest);
    color: #fff;
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    box-shadow: 6px 6px 0 0 var(--color-ink);
  }

  .sc-glow {
    position: absolute;
    top: -10rem;
    right: -10rem;
    width: 40rem;
    height: 40rem;
    border-radius: 999px;
    opacity: 0.4;
    filter: blur(64px);
    background: radial-gradient(circle, #14513a 0%, transparent 65%);
    pointer-events: none;
  }

  .sc-hero-grid {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: 1fr;
    gap: 3rem;
    align-items: center;
    padding: 3.5rem 1.5rem 4rem;
  }

  .sc-hero :global(.eyebrow) {
    color: var(--color-lime);
  }

  .sc-title {
    margin: 1.5rem 0 0;
    font-size: clamp(2.9rem, 7.4vw, 6.4rem);
    line-height: 0.92;
    color: #fff;
  }

  .sc-line {
    display: block;
  }

  .sc-lime {
    color: var(--color-lime);
  }

  .sc-dim {
    color: rgb(255 255 255 / 0.55);
  }

  .sc-lede {
    margin: 2rem 0 0;
    max-width: 36rem;
    font-size: 1.125rem;
    line-height: 1.7;
    color: rgb(255 255 255 / 0.8);
  }

  .sc-lede strong {
    color: #fff;
  }

  .sc-cta {
    margin-top: 2.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 1.25rem;
  }

  .sc-hero-btn {
    box-shadow: 4px 4px 0 0 #fff;
  }

  .sc-hero-btn:hover:not(:disabled) {
    box-shadow: 6px 6px 0 0 #fff;
  }

  .sc-fig-wrap {
    position: relative;
    width: 100%;
    max-width: 28rem;
    margin: 0 auto;
  }

  .sc-fig {
    border: 1px solid rgb(215 255 68 / 0.3);
    border-radius: 28px;
    background: rgb(14 58 41 / 0.7);
    backdrop-filter: blur(8px);
    padding: 1.5rem;
  }

  .sc-fig-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: rgb(215 255 68 / 0.8);
  }

  .sc-live {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
  }

  .sc-live-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 999px;
    background: var(--color-lime);
    animation: sc-pulse-ring 2.4s ease-out infinite;
  }

  .sc-stat-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
    margin-top: 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }

  .sc-stat {
    border-radius: 12px;
    border: 1px solid;
    padding: 0.75rem;
  }

  .sc-stat-flag {
    border-color: rgb(255 74 43 / 0.6);
    background: rgb(255 74 43 / 0.1);
  }

  .sc-stat-flag .sc-stat-cap {
    color: var(--color-flag);
  }

  .sc-stat-lime {
    border-color: rgb(215 255 68 / 0.5);
    background: rgb(215 255 68 / 0.1);
  }

  .sc-stat-lime .sc-stat-cap {
    color: var(--color-lime);
  }

  .sc-stat-cap {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .sc-stat-num {
    margin-top: 0.25rem;
    font-size: 2.25rem;
    line-height: 1;
    color: #fff;
  }

  .sc-stat-unit {
    font-size: 1rem;
    color: rgb(255 255 255 / 0.6);
  }

  .sc-badge-float {
    display: none;
    position: absolute;
    top: 6rem;
    left: -1rem;
    border: 2px solid var(--color-ink);
    border-radius: 999px;
    background: var(--color-lime);
    color: var(--color-ink);
    box-shadow: 4px 4px 0 0 #000;
    padding: 0.5rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
    animation: sc-float-y 5s ease-in-out infinite;
  }

  .sc-building {
    width: 100%;
    height: auto;
    display: block;
  }

  /* ---- Marquee (ref StartView.tsx:135-144) ---- */
  .sc-marquee {
    margin-top: 1.5rem;
    overflow: hidden;
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    background: var(--color-lime);
    color: var(--color-ink);
    padding: 0.875rem 0;
  }

  .sc-marquee-track {
    display: flex;
    width: max-content;
    gap: 2.5rem;
    white-space: nowrap;
    animation: sc-marquee 38s linear infinite;
  }

  .sc-rule {
    display: flex;
    align-items: center;
    gap: 2.5rem;
    font-family: var(--font-mono);
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .sc-diamond {
    color: var(--color-forest-3);
  }

  /* ---- How (ref StartView.tsx:149-174) ---- */
  .sc-how {
    padding: 6rem 0;
    scroll-margin-top: 1.5rem;
  }

  .sc-section-title {
    margin: 1rem 0 0;
    max-width: 48rem;
    font-size: clamp(2.2rem, 5vw, 4rem);
    line-height: 0.98;
  }

  .sc-steps {
    margin-top: 3.5rem;
    display: grid;
    grid-template-columns: 1fr;
    gap: 1px;
    background: var(--color-ink);
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    overflow: hidden;
  }

  .sc-step {
    background: var(--color-card);
    padding: 2rem;
    transition: background 160ms ease;
  }

  .sc-step:hover {
    background: var(--color-lime);
  }

  .sc-step-n {
    display: block;
    font-size: 5.5rem;
    line-height: 1;
    color: rgb(11 21 16 / 0.15);
    transition: color 160ms ease;
  }

  .sc-step:hover .sc-step-n {
    color: rgb(11 21 16 / 0.3);
  }

  .sc-step-t {
    margin: 1.5rem 0 0;
    font-size: 1.875rem;
    line-height: 1.1;
    font-weight: 400;
  }

  .sc-step-d {
    margin: 0.75rem 0 0;
    font-size: 0.98rem;
    line-height: 1.6;
    color: rgb(11 21 16 / 0.7);
  }

  .sc-step:hover .sc-step-d {
    color: rgb(11 21 16 / 0.85);
  }

  /* ---- Check band (ref StartView.tsx:427-452) ---- */
  .sc-check {
    position: relative;
    overflow: hidden;
    background: var(--color-paper-2);
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    scroll-margin-top: 1.5rem;
  }

  .sc-check-grid {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: 1fr;
    gap: 3rem;
    padding: 4rem 1.5rem;
  }

  .sc-check-title {
    margin: 1rem 0 0;
    font-size: clamp(2.2rem, 4.6vw, 3.8rem);
    line-height: 0.98;
  }

  .sc-check-lede {
    margin: 1.5rem 0 0;
    max-width: 28rem;
    font-size: 1.125rem;
    color: rgb(11 21 16 / 0.75);
  }

  .sc-points {
    list-style: none;
    margin: 2rem 0 0;
    padding: 0;
    display: grid;
    gap: 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.8rem;
  }

  .sc-points li {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
  }

  .sc-tick {
    margin-top: 0.125rem;
    display: grid;
    place-items: center;
    flex: none;
    width: 1.25rem;
    height: 1.25rem;
    border-radius: 999px;
    background: var(--color-ink);
    color: var(--color-lime);
    font-size: 0.65rem;
  }

  .sc-form {
    background: var(--color-card);
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    padding: 1.5rem;
  }

  .sc-form-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 2rem;
  }

  .sc-pill {
    border-radius: 999px;
    background: var(--color-ink);
    color: var(--color-lime);
    padding: 0.25rem 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .sc-eta {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .sc-label {
    display: block;
    margin: 0 0 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-ink);
  }

  .sc-label-gap {
    margin-top: 2rem;
  }

  .sc-opt {
    color: var(--color-muted);
    font-weight: 400;
    text-transform: none;
    letter-spacing: normal;
  }

  .sc-hint {
    display: block;
    margin-top: 0.5rem;
    font-size: 0.82rem;
    color: var(--color-muted);
  }

  .sc-urls {
    font-family: var(--font-mono);
    font-size: 0.88rem;
    line-height: 1.6;
  }

  .sc-drop {
    position: relative;
    margin-top: 0.75rem;
    display: grid;
    justify-items: center;
    gap: 0.25rem;
    text-align: center;
    border: 2px dashed rgb(11 21 16 / 0.4);
    border-radius: 16px;
    background: var(--color-paper);
    padding: 2rem 1rem;
    cursor: pointer;
    transition: background 160ms ease, border-color 160ms ease, transform 160ms ease;
  }

  .sc-drop:hover {
    border-color: var(--color-ink);
    background: rgb(215 255 68 / 0.3);
  }

  .sc-drop:focus-within {
    outline: 2px solid var(--color-forest-3);
    outline-offset: 2px;
  }

  .sc-drop strong {
    margin-top: 0.25rem;
    font-size: 0.98rem;
  }

  .sc-drop-icon {
    display: grid;
    place-items: center;
    width: 2.75rem;
    height: 2.75rem;
    border: 2px solid var(--color-ink);
    border-radius: 999px;
    background: var(--color-lime);
    font-size: 1.125rem;
    font-weight: 700;
  }

  .sc-drop-hint {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .sc-drop-input {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    opacity: 0;
    cursor: pointer;
  }

  .sc-chips {
    list-style: none;
    margin: 0.75rem 0 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .sc-chip {
    border: 2px solid var(--color-ink);
    border-radius: 999px;
    background: var(--color-lime);
    padding: 0.25rem 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
    overflow-wrap: anywhere;
  }

  .sc-video-pick {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem;
    margin: 0.75rem 0 0;
  }

  .sc-link-btn {
    border: 0;
    background: transparent;
    padding: 0;
    font: inherit;
    font-weight: 700;
    color: var(--color-muted);
    text-decoration: underline;
    text-underline-offset: 4px;
    cursor: pointer;
  }

  .sc-link-btn:hover {
    color: var(--color-ink);
  }

  .sc-videobox {
    margin-top: 1.25rem;
    border: 2px solid rgb(11 21 16 / 0.2);
    border-radius: 16px;
    background: var(--color-paper);
    padding: 1.25rem;
  }

  .sc-error {
    margin: 1.5rem 0 0;
    border: 2px solid var(--color-flag);
    border-radius: 12px;
    background: rgb(255 74 43 / 0.1);
    padding: 0.75rem 1rem;
    font-weight: 600;
    color: var(--color-flag);
  }

  .sc-submit {
    margin-top: 2.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 1.25rem;
  }

  .sc-submit-btn {
    width: 100%;
  }

  .sc-no-acct {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  /* ---- Recents (ref StartView.tsx:454-493) ---- */
  .sc-recents {
    padding-top: 6rem;
  }

  .sc-recent-title {
    margin: 1rem 0 0;
    font-size: 2.25rem;
    line-height: 1.05;
  }

  .sc-recent-list {
    list-style: none;
    margin: 2rem 0 0;
    padding: 0;
    display: grid;
    gap: 0.75rem;
  }

  .sc-recent-pager {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 1rem;
  }

  .sc-recent-page {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .sc-recent-link {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    min-height: 4rem;
    border: 2px solid var(--color-ink);
    border-radius: 16px;
    background: var(--color-card);
    padding: 0.75rem 1.25rem;
    text-decoration: none;
    transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
  }

  .sc-recent-link:hover {
    transform: translateY(-2px);
    background: var(--color-lime);
    box-shadow: 4px 4px 0 0 var(--color-ink);
  }

  .sc-recent-name {
    font-size: 1.25rem;
    font-weight: 400;
    color: var(--color-ink);
  }

  .sc-recent-date {
    display: block;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .sc-recent-note {
    margin: 0.75rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .sc-recent-link:hover .sc-recent-date {
    color: rgb(11 21 16 / 0.7);
  }

  .sc-tone {
    border-radius: 999px;
    padding: 0.125rem 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .sc-tone-ok {
    background: var(--color-ok);
    color: #fff;
  }

  .sc-tone-flag {
    background: var(--color-flag);
    color: #fff;
  }

  .sc-tone-unsure {
    background: var(--color-amber);
    color: var(--color-ink);
  }

  .sc-clear {
    margin-top: 1rem;
    min-height: 2.75rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }

  /* ---- Samples (ref StartView.tsx:495-540) ---- */
  .sc-samples {
    padding-top: 6rem;
    scroll-margin-top: 1.5rem;
  }

  .sc-sample-grid {
    margin-top: 3rem;
    display: grid;
    grid-template-columns: 1fr;
    gap: 2rem;
  }

  .sc-sample-cell {
    height: 100%;
  }

  .sc-sample-card {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    border: 2px solid var(--color-ink);
    border-radius: 28px;
    text-decoration: none;
    transition: transform 160ms ease, box-shadow 160ms ease;
  }

  .sc-sample-card:hover {
    transform: translateY(-4px);
    box-shadow: 8px 8px 0 0 var(--color-ink);
  }

  .sc-flagged {
    background: var(--color-forest);
    color: #fff;
  }

  .sc-clean {
    background: var(--color-card);
    color: var(--color-ink);
  }

  .sc-sample-art {
    position: relative;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    height: 15rem;
    border-bottom: 2px solid var(--color-ink);
    overflow: hidden;
  }

  .sc-flagged .sc-sample-art {
    background-color: var(--color-forest);
    color: #fff;
  }

  .sc-clean .sc-sample-art {
    background-color: rgb(215 255 68 / 0.3);
  }

  .sc-sample-art .sc-building {
    height: 100%;
    width: auto;
    max-width: 100%;
  }

  .sc-sample-badge {
    position: absolute;
    top: 1rem;
    left: 1rem;
    border: 2px solid;
    border-radius: 999px;
    padding: 0.125rem 0.75rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .sc-badge-flag {
    border-color: var(--color-flag);
    background: var(--color-flag);
    color: #fff;
  }

  .sc-badge-ok {
    border-color: var(--color-ok);
    background: var(--color-ok);
    color: #fff;
  }

  .sc-sample-body {
    display: flex;
    flex-direction: column;
    flex: 1;
    padding: 1.75rem;
  }

  .sc-sample-body :global(.eyebrow) {
    margin-bottom: 0;
  }

  .sc-flagged .sc-constructed {
    color: var(--color-lime);
  }

  .sc-sample-name {
    margin-top: 0.5rem;
    font-size: 1.875rem;
    line-height: 1.05;
    font-weight: 400;
  }

  .sc-sample-head {
    margin: 0.75rem 0 0;
    flex: 1;
    font-size: 0.95rem;
  }

  .sc-flagged .sc-sample-head {
    color: rgb(255 255 255 / 0.7);
  }

  .sc-clean .sc-sample-head {
    color: rgb(11 21 16 / 0.7);
  }

  .sc-open {
    margin-top: 1.5rem;
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 700;
  }

  .sc-flagged .sc-open {
    color: var(--color-lime);
  }

  .sc-clean .sc-open {
    color: var(--color-forest-3);
  }

  .sc-arrow {
    display: inline-block;
    transition: transform 160ms ease;
  }

  .sc-sample-card:hover .sc-arrow {
    transform: translateX(6px);
  }

  .sc-disclaimer {
    margin: 5rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  /* ---- Motion (ref ui.tsx + index.css; CSS only, no new deps) ---- */
  .sc-reveal {
    opacity: 0;
    transform: translateY(28px);
  }

  .sc-reveal.is-visible {
    opacity: 1;
    transform: none;
    transition:
      opacity 0.7s var(--ease-out),
      transform 0.7s var(--ease-out),
      background 160ms ease;
    transition-delay: var(--sc-delay, 0ms), var(--sc-delay, 0ms), 0ms;
  }

  .sc-enter {
    opacity: 0;
    animation: sc-rise 0.9s var(--ease-out) forwards;
    animation-delay: var(--sc-delay, 0ms);
  }

  @keyframes sc-rise {
    from {
      opacity: 0;
      transform: translateY(40px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  @keyframes sc-marquee {
    from {
      transform: translateX(0);
    }
    to {
      transform: translateX(-50%);
    }
  }

  @keyframes sc-pulse-ring {
    0% {
      box-shadow: 0 0 0 0 rgb(215 255 68 / 0.55);
    }
    100% {
      box-shadow: 0 0 0 16px rgb(215 255 68 / 0);
    }
  }

  @keyframes sc-float-y {
    0%,
    100% {
      transform: translateY(0);
    }
    50% {
      transform: translateY(-8px);
    }
  }

  @media (min-width: 640px) {
    .sc-hero-grid {
      padding: 3.5rem 2rem 4rem;
    }

    .sc-badge-float {
      display: block;
    }

    .sc-form {
      padding: 2.5rem;
    }

    .sc-submit-btn {
      width: auto;
    }
  }

  @media (min-width: 768px) {
    .sc-steps {
      grid-template-columns: repeat(3, 1fr);
    }

    .sc-step {
      padding: 2.5rem;
    }

    .sc-sample-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (min-width: 1024px) {
    .sc-hero-grid {
      grid-template-columns: 1.15fr 0.85fr;
      padding: 5rem 2rem 6rem;
    }

    .sc-check-grid {
      grid-template-columns: 0.8fr 1.2fr;
      padding: 6rem 2rem;
    }

    .sc-check-intro {
      position: sticky;
      top: 2rem;
      align-self: start;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .sc-reveal,
    .sc-enter {
      opacity: 1;
      transform: none;
      animation: none;
      transition: none;
    }

    .sc-marquee-track,
    .sc-live-dot,
    .sc-badge-float {
      animation: none;
    }
  }
</style>
