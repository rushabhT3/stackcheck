<script lang="ts">
  import { ApiError, getReport, getSamples, getStatus, mediaUrl, userMessage } from "../lib/api";
  import { formatCrore, formatDate } from "../lib/format";
  import type { PhotoRecord, Report } from "../lib/types";
  import VerdictBanner from "../components/VerdictBanner.svelte";
  import FindingCard, { type PhotoMeta } from "../components/FindingCard.svelte";
  import QuarterTimeline from "../components/QuarterTimeline.svelte";
  import MoneyChart from "../components/MoneyChart.svelte";
  import CantVerifyList from "../components/CantVerifyList.svelte";
  import OverlayThumb from "../components/OverlayThumb.svelte";
  import AgentTrace from "../components/AgentTrace.svelte";
  import Disclaimer from "../components/Disclaimer.svelte";
  import CopyLink from "../components/CopyLink.svelte";

  let { id }: { id: string } = $props();

  const link = $derived(
    `${window.location.origin}${window.location.pathname}#/report/${id}`,
  );

  let report = $state<Report | null>(null);
  let error = $state<string | null>(null);
  let notFound = $state(false);
  let notReady = $state(false);
  let failedCheck = $state(false);
  let hindi = $state(false);
  let constructed = $state(false);

  // Collapsible long sections; all expanded by default, memory-only.
  let collapsed = $state({ flags: false, confirmed: false, quarters: false });

  function toggleSection(key: "flags" | "confirmed" | "quarters"): void {
    collapsed[key] = !collapsed[key];
  }

  $effect(() => {
    let alive = true;
    document.title = "StackCheck";
    getReport(id)
      .then((r) => {
        if (!alive) return;
        report = r;
        document.title = `${r.project_label} report | StackCheck`;
      })
      .catch(async (e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError && e.code === "check_not_found") {
          notFound = true;
          document.title = "Report not found | StackCheck";
        } else if (e instanceof ApiError && e.code === "report_not_ready") {
          notReady = true;
          try {
            const st = await getStatus(id);
            failedCheck = st.state === "failed";
          } catch {
            failedCheck = false;
          }
          document.title = failedCheck
            ? "Check didn't finish | StackCheck"
            : "Report not ready | StackCheck";
        } else {
          error = userMessage(e);
        }
      });
    getSamples()
      .then((s) => {
        if (!alive) return;
        constructed = s.some((x) => x.check_id === id && x.constructed);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  });

  const flags = $derived(report?.findings.filter((f) => f.status === "FLAG") ?? []);
  const oks = $derived(report?.findings.filter((f) => f.status === "OK") ?? []);
  const metas = $derived(report ? photosOf(report) : []);
  const docUrls = $derived(report ? docUrlsOf(report) : {});
  const sitePhotos = $derived(report?.evidence.photos ?? []);
  const countOnly = $derived(
    report !== null &&
      report.evidence.quarters.length === 0 &&
      (sitePhotos.length > 0 || report.evidence.video != null),
  );
  const userFloors = $derived(report ? userFloorsOf(report) : null);

  let expanded = $state<Record<string, boolean>>({});

  function photosOf(r: Report): PhotoMeta[] {
    const metas: PhotoMeta[] = [];
    for (const q of r.evidence.quarters) {
      for (const p of q.photos) {
        const rec: PhotoRecord = p;
        metas.push({
          imageId: rec.image.image_id,
          overlayPath: rec.overlay_path,
          quarterEnd: q.filing.quarter_end,
          rowsMarked: rec.floors?.sam_rows ?? null,
        });
      }
    }
    return metas;
  }

  /** Floors entered on the check form, when any block carries them. */
  function userFloorsOf(r: Report): number | null {
    if (typeof r.evidence.floors_above_ground === "number") return r.evidence.floors_above_ground;
    for (const q of r.evidence.quarters) {
      for (const b of q.filing.blocks) {
        if (b.floors_entered_by_user !== null) return b.floors_entered_by_user;
      }
    }
    return null;
  }

  /** Public source URL per filing, for page links. Uploads have no public link. */
  function docUrlsOf(r: Report): Record<string, string | null> {
    const urls: Record<string, string | null> = {};
    for (const q of r.evidence.quarters) {
      const src = q.source;
      urls[q.filing.doc_id] =
        src.origin === "url" && src.url ? src.url : null;
    }
    return urls;
  }

  // Sticky section nav (ref ReportView.tsx:36-43,154-163). Buttons scroll
  // in-page only; routes stay untouched.
  const NAV_ITEMS = [
    { key: "flags", label: "Flags" },
    { key: "confirmed", label: "Confirmed" },
    { key: "cant-verify", label: "Can't verify" },
    { key: "quarters", label: "Quarters" },
    { key: "video", label: "Video" },
    { key: "trace", label: "Trace" },
  ] as const;

  type NavKey = (typeof NAV_ITEMS)[number]["key"];

  const hasVideo = $derived(report?.evidence.video != null);
  const hasTrace = $derived((report?.trace.length ?? 0) > 0);
  const navItems = $derived(
    NAV_ITEMS.filter((item) =>
      countOnly &&
      (item.key === "flags" ||
        item.key === "confirmed" ||
        item.key === "cant-verify" ||
        item.key === "quarters")
        ? false
        : item.key === "video"
          ? hasVideo
          : item.key === "trace"
            ? hasTrace
            : true,
    ),
  );

  function scrollToSection(key: NavKey): void {
    document.getElementById(key)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
</script>

{#snippet building(floors: number, total: number = 8, markers: boolean = true)}
  {@const count = Math.max(total, floors, 1)}
  {@const groundY = 262}
  {@const fh = Math.min(20, (groundY - 34) / count)}
  {@const x0 = 62}
  {@const w = 96}
  <svg viewBox="0 0 220 290" class="rp-building" role="img" aria-label={`Building sketch with ${floors} floors visible`}>
    <line x1="14" y1={groundY} x2="206" y2={groundY} stroke="rgb(255 255 255 / 0.5)" stroke-width="2" />
    {#each Array.from({ length: 10 }, (_, i) => i) as i (i)}
      <line x1={20 + i * 20} y1={groundY} x2={12 + i * 20} y2={groundY + 8} stroke="rgb(215 255 68 / 0.35)" stroke-width="1" />
    {/each}
    <g stroke="rgb(215 255 68 / 0.35)" stroke-width="1.5" fill="none">
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
          fill={built ? "rgb(215 255 68 / 0.10)" : "none"}
          stroke={built ? "#eaf6e2" : "rgb(215 255 68 / 0.35)"}
          stroke-width={built ? 1.5 : 1}
          stroke-dasharray={built ? undefined : "3 4"}
        />
        {#if built}
          {#each [0, 1, 2, 3] as k (k)}
            <rect x={x0 + 9 + k * 22} y={y + fh * 0.28} width="12" height={fh * 0.44} fill="#d7ff44" opacity="0.85" />
          {/each}
          {#if markers}
            <line x1={x0 - 10} y1={y + fh / 2} x2={x0 - 2} y2={y + fh / 2} stroke="#d7ff44" stroke-width="2" />
            <text x={x0 - 14} y={y + fh / 2 + 3.5} text-anchor="end" font-size="9.5" font-family="var(--font-mono)" font-weight="700" fill="#d7ff44">
              {i + 1}
            </text>
          {/if}
        {/if}
      </g>
    {/each}
  </svg>
{/snippet}

{#if notFound}
  <section class="rp-notice" aria-labelledby="missing-heading">
    <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
    <span class="eyebrow">404 · Report</span>
    <h1 id="missing-heading" class="display rp-notice-title">Report <em>not found.</em></h1>
    <div class="rp-notice-body">
      <p>We can't find that report. The link may be wrong, or it was made before the last reset.</p>
      <p><a class="btn" href="#/">Start a new check →</a></p>
    </div>
  </section>
{:else if notReady}
  {#if failedCheck}
    <section class="rp-notice" aria-labelledby="notready-heading">
      <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
      <span class="eyebrow">Check failed</span>
      <h1 id="notready-heading" class="display rp-notice-title">This check didn't finish.</h1>
      <div class="rp-notice-body">
        <p>The check failed before it could build a report.</p>
        <p class="rp-notice-links">
          <a class="link-u" href={`#/check/${id}`}>See what went wrong</a> ·
          <a class="link-u" href="#/">Start a new check</a>
        </p>
      </div>
    </section>
  {:else}
    <section class="rp-notice" aria-labelledby="notready-heading">
      <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
      <span class="eyebrow">Still running</span>
      <h1 id="notready-heading" class="display rp-notice-title">Report <em>not ready yet.</em></h1>
      <div class="rp-notice-body">
        <p>This check is still running. It will appear here when it finishes.</p>
        <p class="rp-notice-links">
          <a class="link-u" href={`#/check/${id}`}>Watch the progress</a> ·
          <a class="link-u" href="#/">Start a new check</a>
        </p>
      </div>
    </section>
  {/if}
{:else if error}
  <section class="rp-notice" aria-labelledby="error-heading">
    <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
    <span class="eyebrow">Error</span>
    <h1 id="error-heading" class="display rp-notice-title">Something <em>went wrong.</em></h1>
    <div class="rp-notice-body">
      <p class="rp-error" role="alert">{error}</p>
      <p class="rp-notice-links"><a class="link-u" href="#/">Start a new check</a></p>
    </div>
  </section>
{:else if report}
  <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
  <VerdictBanner verdict={report.verdict} costs={report.costs} countOnly={countOnly} />

  <nav class="rp-nav" aria-label="Report sections">
    <div class="rp-nav-inner scroll-x">
      {#each navItems as item (item.key)}
        <button type="button" class="rp-pill" onclick={() => scrollToSection(item.key)}>
          {item.label}
        </button>
      {/each}
    </div>
  </nav>

  <div class="rp-head">
    <div class="rp-head-main">
      <p class="display rp-project">{report.project_label}</p>
      <p class="rp-checkline">
        Check {report.check_id} · created {formatDate(report.created_at.slice(0, 10))} ·
        <a href="#/accuracy" class="link-u rp-checkline-link">How accurate is this?</a>
      </p>
      {#if constructed}
        <p class="rp-constructed" role="note">
          ◆ Constructed example — built to show the layout, not from a real filing.
        </p>
      {/if}
    </div>
    <CopyLink url={link} />
  </div>

  {#if report.summary_en}
    <section class="rp-summary" aria-labelledby="summary-heading">
      <div class="rp-summary-top">
        <span class="eyebrow">Plain-language summary</span>
        {#if report.summary_hi}
          <label class="rp-lang">
            <input type="checkbox" bind:checked={hindi} />
            हिन्दी में पढ़ें
          </label>
        {/if}
      </div>
      <h2 id="summary-heading" class="rp-sr">Summary</h2>
      <p class="display rp-lede">{hindi && report.summary_hi ? report.summary_hi : report.summary_en}</p>
    </section>
  {/if}

  {#if !countOnly}
  <section id="flags" class="rp-section" aria-labelledby="flags-heading">
    <div class="rp-sechead">
      <div>
        <span class="eyebrow">Needs a question</span>
        <h2 id="flags-heading" class="display rp-sectitle">
          <button
            type="button"
            class="rp-toggle"
            aria-expanded={!collapsed.flags}
            aria-controls="flags-body"
            onclick={() => toggleSection("flags")}
          >
            <span>Flags</span>
            <span class="rp-arrow" class:rp-collapsed={collapsed.flags} aria-hidden="true">▾</span>
          </button>
        </h2>
      </div>
      <span class="display rp-count" aria-hidden="true">{String(flags.length).padStart(2, "0")}</span>
    </div>
    {#if !collapsed.flags}
      <div id="flags-body">
        {#if flags.length === 0}
          <p class="rp-empty-ok">No flags in this check.</p>
        {:else}
          {#each flags as f (f.finding_id)}
            <FindingCard finding={f} checkId={report.check_id} photos={metas} {docUrls} />
          {/each}
        {/if}
      </div>
    {/if}
  </section>
  {/if}

  {#if !countOnly}
  <section id="confirmed" class="rp-section" aria-labelledby="confirmed-heading">
    <div class="rp-sechead">
      <div>
        <span class="eyebrow">Checked and clean</span>
        <h2 id="confirmed-heading" class="display rp-sectitle">
          <button
            type="button"
            class="rp-toggle"
            aria-expanded={!collapsed.confirmed}
            aria-controls="confirmed-body"
            onclick={() => toggleSection("confirmed")}
          >
            <span>Confirmed</span>
            <span class="rp-arrow" class:rp-collapsed={collapsed.confirmed} aria-hidden="true">▾</span>
          </button>
        </h2>
      </div>
      <span class="display rp-count" aria-hidden="true">{String(oks.length).padStart(2, "0")}</span>
    </div>
    {#if !collapsed.confirmed}
      <div id="confirmed-body">
        {#if oks.length === 0}
          <p>Nothing was confirmed in this check.</p>
        {:else}
          <details class="rp-okbox">
            <summary>
              <span class="rp-oklabel">
                <span class="rp-check" aria-hidden="true">✓</span>{oks.length} confirmed — show the evidence
              </span>
              <span class="rp-plus" aria-hidden="true">+</span>
            </summary>
            <div class="rp-oklist">
              {#each oks as f (f.finding_id)}
                <FindingCard finding={f} checkId={report.check_id} photos={metas} {docUrls} />
              {/each}
            </div>
          </details>
        {/if}
      </div>
    {/if}
  </section>
  {/if}

  {#if !countOnly}
  <div id="cant-verify" class="rp-section">
    <CantVerifyList findings={report.findings} checkId={report.check_id} photos={metas} />
  </div>
  {/if}

  {#if !countOnly}
  <section id="quarters" class="rp-section" aria-labelledby="quarters-heading">
    <div class="rp-sechead">
      <div>
        <span class="eyebrow">The raw filings</span>
        <h2 id="quarters-heading" class="display rp-sectitle">
          <button
            type="button"
            class="rp-toggle"
            aria-expanded={!collapsed.quarters}
            aria-controls="quarters-body"
            onclick={() => toggleSection("quarters")}
          >
            <span>Quarters</span>
            <span class="rp-arrow" class:rp-collapsed={collapsed.quarters} aria-hidden="true">▾</span>
          </button>
        </h2>
      </div>
      <span class="display rp-count" aria-hidden="true">{String(report.evidence.quarters.length).padStart(2, "0")}</span>
    </div>
    {#if !collapsed.quarters}
    <div id="quarters-body">
    <div class="rp-qgrid">
      {#each report.evidence.quarters as q, qi (q.filing.doc_id)}
        {@const f = q.filing.financials}
        {@const spentUnit = f.unit_overrides?.["cumulative_spent"] ?? f.currency_unit}
        {@const totalUnit = f.unit_overrides?.["estimated_total_cost"] ?? f.currency_unit}
        {@const elev =
          q.photos.find((p) => p.facts?.kind === "elevation" && p.overlay_path) ??
          q.photos.find((p) => p.overlay_path)}
        {@const elevRows = elev?.floors?.sam_rows ?? null}
        <article class="rp-qcard rp-rise" style={`animation-delay: ${(qi % 2) * 0.08}s`}>
          <div class="rp-qtop">
            <div>
              <span class="eyebrow rp-qkick">Filing {qi + 1} of {report.evidence.quarters.length}</span>
              <h3 class="display rp-qtitle" title={q.filing.doc_id}>
                Quarter ending {formatDate(q.filing.quarter_end)}
              </h3>
            </div>
            <span class="display rp-qnum" aria-hidden="true">Q{qi + 1}</span>
          </div>
          {#if q.filing.notes && q.filing.notes.length > 0}
            <ul class="rp-qnotes">
              {#each q.filing.notes as note}
                {#if /^\d+ values? could not be matched/.test(note)}
                  <li>
                    <details class="rp-notebox">
                      <summary class="rp-notewarn">⚠ {note}</summary>
                      <p class="rp-notemeta">The check left these values out instead of guessing.</p>
                    </details>
                  </li>
                {:else}
                  <li>{note}</li>
                {/if}
              {/each}
            </ul>
          {/if}
          <dl class="rp-qlist">
            {#each q.filing.blocks as b (b.block_label)}
              <div class="rp-qrow">
                <dt class="rp-qdt">Block {b.block_label.replace(/^\s*block\s*[-–—:]?\s*/i, "")}</dt>
                <dd class="rp-qdd">
                  {#if b.slabs_claimed_count === null}No slab count claimed{:else}<strong>{b.slabs_claimed_count}</strong> slabs claimed{/if}
                  ·
                  {#if b.floors_above_ground_sanctioned === null}Floors sanctioned: not reported{:else}<strong>{b.floors_above_ground_sanctioned}</strong> floors sanctioned{/if}
                </dd>
              </div>
            {/each}
            <div class="rp-qrow">
              {#if f.cumulative_spent === null || f.cumulative_spent === ""}
                <dd class="rp-qdd">
                  Money spent: not reported (estimated project cost
                  {#if f.estimated_total_cost === null || f.estimated_total_cost === ""}not reported{:else}{formatCrore(
                      f.estimated_total_cost,
                      totalUnit,
                    )}{/if})
                </dd>
              {:else}
                <dt class="rp-qdt">Money spent</dt>
                <dd class="rp-qdd">
                  {#if f.estimated_total_cost === null || f.estimated_total_cost === ""}<strong
                      >{formatCrore(f.cumulative_spent, spentUnit)}</strong
                    >
                    (estimated project cost not reported)
                  {:else}<strong>{formatCrore(f.cumulative_spent, spentUnit)}</strong> of
                    {formatCrore(f.estimated_total_cost, totalUnit)} estimated
                  {/if}
                </dd>
              {/if}
            </div>
            <div class="rp-qrow">
              <dt class="rp-qdt">Photos</dt>
              <dd class="rp-qdd">
                {q.photos.length} photo{q.photos.length === 1 ? "" : "s"} ·
                {q.photos.filter((p) => p.facts?.kind === "elevation").length} elevation
                {#if elev?.overlay_path}
                  <OverlayThumb
                    checkId={report.check_id}
                    overlayPath={elev.overlay_path}
                    alt={elevRows === null
                      ? "Elevation photo"
                      : `Elevation photo with ${elevRows} counted floor rows`}
                  />
                {/if}
              </dd>
            </div>
          </dl>
        </article>
      {/each}
    </div>
    {#if !countOnly}
    <div class="rp-charts">
      <div class="rp-rise"><QuarterTimeline quarters={report.evidence.quarters} /></div>
      <div class="rp-rise" style="animation-delay: 0.1s"><MoneyChart quarters={report.evidence.quarters} /></div>
    </div>
    {/if}
    </div>
    {/if}
  </section>
  {/if}

  {@const video = report.evidence.video}
  {#if video}
    <section id="video" class="rp-section" aria-labelledby="video-heading">
      <div class="rp-sechead">
        <div>
          <span class="eyebrow">Your walkthrough</span>
          <h2 id="video-heading" class="display rp-sectitle">Site video</h2>
        </div>
      </div>
      <div class="rp-video">
        {#if video.contact_sheet}
          <img
            src={mediaUrl(report.check_id, video.contact_sheet)}
            alt="Contact sheet of the clearest video frames with counted floor rows"
            class="rp-still"
          />
        {/if}
        <div class="rp-vbody">
          {#if video.tier && video.lower !== null && video.upper !== null}
            <p class="display rp-vstate">
              Counted from your video: about
              <em>{video.lower === video.upper ? video.lower : `${video.lower} to ${video.upper}`}</em> floors
              <span class="rp-vtier">({video.tier === "CONFIDENT" ? "confident" : "estimate"})</span>.
            </p>
            <p class="rp-vmeta">
              {#if video.lower !== video.upper}Range {video.lower} to {video.upper} floors · {/if}{video.frames_used}
              of {video.frame_counts.length} frames used · uploaded on {formatDate(video.uploaded_on)}
            </p>
          {:else}
            <p>The frames did not give a usable count.</p>
          {/if}
          {#if video.frame_counts.length > 0}
            <div class="rp-frames">
              <details>
                <summary>Show the count for each frame</summary>
                <p>{video.frame_counts.join(", ")}</p>
              </details>
            </div>
          {/if}
          {#if video.credit}
            <p class="rp-vmeta">{video.credit}</p>
          {/if}
        </div>
      </div>
    </section>
  {/if}

  {#if sitePhotos.length > 0}
    <section id="photos" class="rp-section" aria-labelledby="photos-heading">
      <div class="rp-sechead">
        <div>
          <span class="eyebrow">Your photos</span>
          <h2 id="photos-heading" class="display rp-sectitle">Site photos</h2>
        </div>
        <span class="display rp-count" aria-hidden="true">{String(sitePhotos.length).padStart(2, "0")}</span>
      </div>
      <div class="rp-qgrid">
        {#each sitePhotos as photo, pi (photo.image.image_id)}
          {@const floors = photo.floors}
          {@const n = floors?.upper ?? floors?.lower ?? null}
          {@const counted = floors?.status === "OK" && n !== null}
          {@const key = photo.image.image_id}
          {@const rows = floors?.sam_rows ?? null}
          {@const visual = floors?.vision_estimate ?? null}
          {@const alt = rows === null ? "Elevation photo" : `Elevation photo with ${rows} counted floor rows`}
          <div class="rp-video">
            <div class="rp-vbody">
              {#if counted}
                {#if floors?.tier === "CONFIDENT"}
                  <p class="display rp-vstate">
                    Counted from your photo: <em>{n}</em> floors.
                  </p>
                {:else}
                  <p class="display rp-vstate">
                    Counted from your photo: about <em>{n}</em> floors
                    <span class="rp-vtier">(estimate)</span>.
                  </p>
                {/if}
              {:else if visual !== null && rows !== null}
                <p>Visual count {visual}. The second method found {rows} rows and disagreed.</p>
              {:else}
                <p>Could not count the floors in your photo.</p>
              {/if}
              {#if counted && userFloors !== null}
                <p>The photo shows {n} of {userFloors} floors.</p>
              {/if}
              <p class="rp-vmeta">Photo {pi + 1} of {sitePhotos.length}</p>
              {#if photo.overlay_path}
                <div class="photo-row">
                  <OverlayThumb checkId={report.check_id} overlayPath={photo.overlay_path} {alt} />
                  <button
                    type="button"
                    class="btn btn-ghost"
                    onclick={() => (expanded[key] = !expanded[key])}
                  >
                    {expanded[key] ? "Hide photo" : "View photo"}
                  </button>
                </div>
              {/if}
              {#if expanded[key] && photo.overlay_path}
                <figure class="photo">
                  <img
                    src={mediaUrl(report.check_id, photo.overlay_path)}
                    {alt}
                    loading="lazy"
                  />
                </figure>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  {#if report.trace.length > 0}
    <section id="trace" class="rp-section">
      <AgentTrace trace={report.trace} />
    </section>
  {/if}
  <Disclaimer text={report.disclaimer} />
{:else}
  <div class="rp-loading on-dark" aria-live="polite">
    <a class="back-link rp-loading-back" href="#/">← Back</a>
    <div class="rp-loading-inner">
      <div class="rp-loading-art animate-float">{@render building(5, 8, false)}</div>
      <p class="eyebrow rp-loading-label">Loading report…</p>
    </div>
  </div>
{/if}

<style>
  /* Notice states (ref ReportView.tsx:12-20 Notice). The App .wrap supplies the
   * container; only vertical rhythm lives here. */
  .rp-notice {
    padding-block: 6rem;
  }

  .rp-notice-title {
    margin: 1.25rem 0 0;
    max-width: 48rem;
    font-size: clamp(2.5rem, 6vw, 5rem);
    line-height: 1;
  }

  .rp-notice-body {
    margin-top: 1.5rem;
    max-width: 36rem;
    font-size: 1.125rem;
    color: rgb(11 21 16 / 0.75);
  }

  .rp-notice-body .btn {
    margin-top: 1rem;
  }

  .rp-notice-links {
    margin-top: 1rem;
  }

  .rp-error {
    font-weight: 600;
    color: var(--color-flag);
  }

  /* Loading panel: full-bleed dark forest (ref ReportView.tsx:134-142). Negative
   * margins cancel the App .wrap padding (1.25rem, 2rem at 40rem). */
  .rp-loading {
    position: relative;
    display: grid;
    place-items: center;
    min-height: 60vh;
    background: var(--color-forest);
    color: #fff;
    margin-inline: -1.25rem;
  }

  .rp-loading-back {
    position: absolute;
    top: 1.25rem;
    left: 1.25rem;
  }

  .rp-loading-inner {
    text-align: center;
    padding: 3rem 1.25rem;
  }

  .rp-loading-art {
    margin: 0 auto;
    height: 6rem;
    width: 6rem;
  }

  .rp-building {
    display: block;
    height: 100%;
    width: 100%;
  }

  .rp-loading-label {
    justify-content: center;
    color: var(--color-lime);
    margin: 1rem 0 0;
  }

  /* Sticky section nav (ref ReportView.tsx:154-163). Full-bleed like the
   * reference; buttons scroll in-page so routes stay untouched. */
  .rp-nav {
    position: sticky;
    top: 0;
    z-index: 20;
    border-bottom: 2px solid var(--color-ink);
    background: rgb(243 239 229 / 0.9);
    backdrop-filter: blur(8px);
    margin-inline: -1.25rem;
    padding-inline: 1.25rem;
  }

  .rp-nav-inner {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    max-width: 78rem;
    margin: 0 auto;
    padding-block: 0.625rem;
  }

  .rp-pill {
    flex: none;
    min-height: 0;
    min-width: 0;
    width: auto;
    padding: 0.375rem 1rem;
    border-radius: 999px;
    border: 2px solid transparent;
    background: transparent;
    box-shadow: none;
    color: rgb(11 21 16 / 0.7);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: border-color 160ms ease, background 160ms ease, color 160ms ease;
  }

  .rp-pill:hover:not(:disabled) {
    border-color: var(--color-ink);
    background: var(--color-lime);
    color: var(--color-ink);
    transform: none;
    box-shadow: none;
  }

  /* Report header block (ref ReportView.tsx:166-180). */
  .rp-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1.5rem;
    padding: 2.5rem 0;
  }

  .rp-head-main {
    display: grid;
    gap: 0.5rem;
  }

  .rp-project {
    margin: 0;
    font-size: 1.875rem;
  }

  .rp-checkline {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .rp-checkline-link {
    font-family: var(--font-mono);
  }

  .rp-constructed {
    margin: 0.5rem 0 0;
    display: inline-flex;
    width: fit-content;
    align-items: center;
    gap: 0.5rem;
    border-radius: 999px;
    border: 2px solid var(--color-ink);
    background: var(--color-lime);
    padding: 0.375rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
  }

  /* Summary card with drop cap (ref ReportView.tsx:182-200). The rise keyframes
   * are global in app.css; reduced-motion disables them there. */
  .rp-summary {
    position: relative;
    border-radius: 28px;
    border: 2px solid var(--color-ink);
    background: var(--color-card);
    padding: 1.75rem;
    box-shadow: 6px 6px 0 0 var(--color-ink);
    animation: rise 0.7s var(--ease-out) both;
  }

  .rp-summary-top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .rp-summary-top .eyebrow {
    margin-bottom: 0;
  }

  .rp-lang {
    display: flex;
    min-height: 2.75rem;
    cursor: pointer;
    align-items: center;
    gap: 0.75rem;
    border-radius: 999px;
    border: 2px solid var(--color-ink);
    background: var(--color-paper);
    padding: 0 1rem;
    font-weight: 600;
  }

  .rp-lang input {
    width: 1.25rem;
    height: 1.25rem;
    accent-color: var(--color-forest-3);
  }

  .rp-sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  .rp-lede {
    margin: 1.5rem 0 0;
    max-width: 56rem;
    font-size: clamp(1.4rem, 2.6vw, 2.15rem);
    line-height: 1.28;
  }

  .rp-lede::first-letter {
    float: left;
    margin-right: 0.75rem;
    font-size: 4.6em;
    line-height: 0.8;
    color: var(--color-forest-3);
  }

  /* Sections + heads (ref SectionHead ReportView.tsx:22-34). */
  .rp-section {
    scroll-margin-top: 5rem;
    padding-top: 5rem;
  }

  .rp-sechead {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    border-bottom: 2px solid var(--color-ink);
    padding-bottom: 1rem;
    margin-bottom: 2rem;
  }

  .rp-sechead .eyebrow {
    margin-bottom: 0;
  }

  .rp-sectitle {
    margin: 0.5rem 0 0;
    font-size: clamp(2rem, 4vw, 3.2rem);
    line-height: 1;
  }

  .rp-count {
    font-size: 3.75rem;
    line-height: 1;
    color: rgb(11 21 16 / 0.15);
  }

  /* Section collapse toggle: resets the global lime-pill button so the
   * title keeps its exact display styling; only the arrow is added. */
  .rp-sectitle .rp-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.75rem;
    min-height: 0;
    min-width: 0;
    width: auto;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: none;
    box-shadow: none;
    font: inherit;
    font-weight: inherit;
    color: inherit;
    cursor: pointer;
  }

  .rp-sectitle .rp-toggle:hover:not(:disabled),
  .rp-sectitle .rp-toggle:active:not(:disabled) {
    transform: none;
    box-shadow: none;
    background: none;
  }

  .rp-arrow {
    display: inline-block;
    font-size: 0.6em;
    line-height: 1;
    color: var(--color-forest-3);
    transition: transform 160ms ease;
  }

  .rp-arrow.rp-collapsed {
    transform: rotate(-90deg);
  }

  .rp-empty-ok {
    margin: 0;
    border-radius: 1rem;
    border: 2px dashed var(--color-ok);
    background: rgb(23 160 93 / 0.1);
    padding: 1.5rem;
    font-weight: 600;
  }

  /* Confirmed disclosure (ref ReportView.tsx:216-224). */
  .rp-okbox {
    padding: 0.5rem;
  }

  .rp-okbox > summary {
    display: flex;
    min-height: 3.5rem;
    cursor: pointer;
    list-style: none;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0 1.25rem;
    font-family: var(--font-mono);
    font-size: 0.875rem;
    font-weight: 700;
  }

  .rp-okbox > summary::-webkit-details-marker {
    display: none;
  }

  .rp-oklabel {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .rp-check {
    display: grid;
    place-items: center;
    height: 1.75rem;
    width: 1.75rem;
    border-radius: 999px;
    background: var(--color-ok);
    color: #fff;
  }

  .rp-plus {
    font-size: 1.5rem;
    line-height: 1;
    color: var(--color-forest-3);
    transition: transform 160ms ease;
  }

  .rp-okbox[open] > summary .rp-plus {
    transform: rotate(45deg);
  }

  .rp-oklist {
    padding: 0 0.5rem 0.5rem;
  }

  /* Quarter cards (ref ReportView.tsx:234-297). */
  .rp-qgrid {
    display: grid;
    gap: 1.25rem;
  }

  .rp-qcard {
    height: 100%;
    border-radius: 22px;
    border: 2px solid var(--color-ink);
    background: var(--color-card);
    padding: 1.5rem;
  }

  .rp-rise {
    animation: rise 0.7s var(--ease-out) both;
  }

  .rp-qtop {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
  }

  .rp-qkick {
    color: var(--color-muted);
    margin-bottom: 0;
  }

  .rp-qtitle {
    margin: 0.25rem 0 0;
    font-size: 1.5rem;
  }

  .rp-qnum {
    font-size: 3rem;
    line-height: 1;
    color: rgb(11 21 16 / 0.15);
  }

  .rp-qnotes {
    margin: 0.75rem 0 0;
    padding: 0;
    list-style: none;
    display: grid;
    gap: 0.25rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .rp-notebox {
    border: 0;
    background: none;
    border-radius: 0;
    margin: 0;
    padding: 0;
  }

  .rp-notebox > summary {
    font-weight: 400;
    padding: 0;
    min-height: 0;
  }

  .rp-notewarn {
    cursor: pointer;
    color: var(--color-cant);
  }

  .rp-notemeta {
    margin: 0.25rem 0 0;
  }

  .rp-qlist {
    margin: 1.25rem 0 0;
    display: grid;
    gap: 1px;
    overflow: hidden;
    border-radius: 0.75rem;
    border: 2px solid var(--color-ink);
    background: var(--color-ink);
    font-size: 0.875rem;
  }

  .rp-qrow {
    background: var(--color-paper);
    padding: 0.75rem 1rem;
  }

  .rp-qdt {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-muted);
  }

  .rp-qdd {
    margin: 0;
  }

  .rp-charts {
    margin-top: 2rem;
    display: grid;
    gap: 1.5rem;
  }

  /* Video card (ref ReportView.tsx:304-347). No demo branch: our api has no
   * demo-mode and the script stays behavior-identical. */
  .rp-video {
    overflow: hidden;
    border-radius: 22px;
    border: 2px solid var(--color-ink);
    background: var(--color-card);
  }

  .rp-still {
    display: block;
    width: 100%;
    height: auto;
    aspect-ratio: 2424 / 668;
  }

  .rp-vbody {
    padding: 1.5rem;
  }

  .rp-vstate {
    margin: 0;
    font-size: clamp(1.5rem, 3vw, 2.4rem);
    line-height: 1.25;
  }

  .rp-vstate em {
    color: var(--color-forest-3);
  }

  .rp-vtier {
    color: var(--color-muted);
  }

  .rp-vmeta {
    margin: 0.75rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .rp-frames details {
    border: 0;
    background: none;
    border-radius: 0;
    margin: 1rem 0 0;
    padding: 0;
    font-family: var(--font-mono);
    font-size: 0.875rem;
  }

  .rp-frames details > summary {
    cursor: pointer;
    font-weight: 700;
    padding: 0;
    min-height: 0;
  }

  .rp-frames p {
    margin: 0.5rem 0 0;
  }

  @media (min-width: 40rem) {
    .rp-loading {
      margin-inline: -2rem;
    }

    .rp-nav {
      margin-inline: -2rem;
      padding-inline: 2rem;
    }

    .rp-summary {
      padding: 3rem;
    }

    .rp-oklist {
      padding: 0 1rem 0.5rem;
    }

    .rp-vbody {
      padding: 2rem;
    }
  }

  @media (min-width: 48rem) {
    .rp-qgrid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (min-width: 64rem) {
    .rp-charts {
      grid-template-columns: repeat(2, 1fr);
    }
  }
</style>
