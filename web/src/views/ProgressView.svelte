<script lang="ts">
  import { untrack } from "svelte";
  import { ApiError, getStatus } from "../lib/api";
  import type { CheckStatus } from "../lib/types";
  import CopyLink from "../components/CopyLink.svelte";

  let { id }: { id: string } = $props();

  let status = $state<CheckStatus | null>(null);
  let failures = $state(0);
  let missing = $state(0);
  let wasDone = false;
  let reportLink = $state<HTMLAnchorElement | null>(null);

  const link = $derived(
    `${window.location.origin}${window.location.pathname}#/check/${id}`,
  );

  function isMissing(error: unknown): boolean {
    return (
      error instanceof ApiError && (error.code === "check_not_found" || error.code === "http_404")
    );
  }

  // One timer per mount. State is read untracked so assigning `status`
  // never resubscribes this effect; the timer stops at a terminal state
  // and is always cleared on unmount.
  $effect(() => {
    const checkId = id;
    let alive = true;
    async function poll(): Promise<void> {
      const current = untrack(() => status);
      if (!alive || current?.state === "done" || current?.state === "failed") return;
      try {
        const next = await getStatus(checkId);
        if (!alive) return;
        status = next;
        failures = 0;
        missing = 0;
        document.title = `Checking ${next.project_label} | StackCheck`;
        if (next.state === "done" || next.state === "failed") {
          window.clearInterval(timer);
          if (next.state === "done" && !wasDone) {
            wasDone = true;
            reportLink?.focus();
          }
        }
      } catch (e) {
        if (!alive) return;
        if (isMissing(e)) {
          missing += 1;
          if (missing >= 2) {
            document.title = "Check not found | StackCheck";
            window.clearInterval(timer);
          }
        } else {
          failures += 1;
        }
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  });

  const doneCount = $derived(
    status ? status.steps.filter((s) => s.state === "done" || s.state === "skipped").length : 0,
  );
  const totalCount = $derived(status ? status.steps.length : 0);
  const wholePercent = $derived(status ? Math.round(status.progress * 100) : 0);
  const builtFloors = $derived(Math.max(1, Math.round((wholePercent / 100) * 14)));

  function icon(state: string): string {
    if (state === "done") return "✓";
    if (state === "running") return "▶";
    if (state === "failed") return "✕";
    if (state === "skipped") return "⊘";
    return "○";
  }

  function stepClass(state: string): string {
    if (state === "done" || state === "skipped") return "is-done";
    if (state === "running") return "is-running";
    if (state === "failed") return "is-failed";
    return "is-pending";
  }
</script>

{#snippet building(floors: number, total: number = 14, claimed: number | null = null, tone: "dark" | "light" = "dark")}
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
  <svg viewBox="0 0 220 290" class="pg-building" role="img" aria-label={`Building sketch with ${floors} floors visible`}>
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

{#if missing >= 2 && !status}
  <section class="pg-shell on-dark noise" aria-labelledby="missing-heading">
    <div class="pg-fill grid-bg" aria-hidden="true"></div>
    <div class="pg-inner">
      <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
      <span class="eyebrow">404 · Check</span>
      <h1 id="missing-heading" class="display pg-title-missing">
        We can't find that check.
      </h1>
      <p class="pg-lede">The link may be wrong, or it was made before the last reset.</p>
      <p class="pg-cta-row">
        <a href="#/" class="btn">Start a new check →</a>
      </p>
    </div>
  </section>
{:else}
  <section class="pg-shell on-dark noise" aria-labelledby="progress-heading">
    <div class="pg-fill grid-bg" aria-hidden="true"></div>
    <div class="pg-inner">
      <p class="back-row"><a class="back-link" href="#/">← Back</a></p>
      <div class="pg-grid">
        <div>
          <span class="eyebrow">
            {status?.state === "done" ? "Check complete" : status?.state === "failed" ? "Check failed" : "Check in progress"}
          </span>
          <h1 id="progress-heading" class="display pg-title">
            {status?.project_label ?? "Loading…"}
          </h1>
          {#if status}
            <div class="pg-copy">
              <CopyLink url={link} />
            </div>
          {/if}
          {#if failures >= 3 && !status}
            <p class="pg-alert" role="alert">
              Something went wrong on our side. The check may have finished, or the server may have
              restarted.
              <a href="#/">Start again</a>
            </p>
          {/if}
          {#if status}
            <div class="pg-score">
              <span class="display pg-num">{wholePercent}</span>
              <span class="display pg-pct">%</span>
              <span class="pg-steps-meta">{doneCount} of {totalCount} steps · {status.state}</span>
            </div>
            <div
              class="pg-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={wholePercent}
              aria-label="Check progress"
            >
              <div class="pg-bar" style={`width: ${wholePercent}%`}>
                {#if status.state !== "done"}
                  <span class="pg-tip"></span>
                {/if}
              </div>
            </div>
            {#if status.state === "queued"}
              <p class="pg-queue">
                {#if status.queue_position}
                  Waiting for a free slot (position {status.queue_position})…
                {:else}
                  Waiting for a free slot…
                {/if}
              </p>
            {/if}
            <ol class="pg-steps">
              {#each status.steps as step, i (step.name)}
                <li class="pg-step {stepClass(step.state)}" style={`animation-delay: ${i * 40}ms`}>
                  <span class="pg-badge" aria-hidden="true">{step.state === "pending" ? i + 1 : icon(step.state)}</span>
                  <span>
                    <span class="display pg-step-name">{step.name}</span>
                    <span class="pg-step-state">{step.state}</span>
                    {#if step.note && !(step.state === "failed" && step.note === status.error)}
                      <span class="pg-step-note">{step.note}</span>
                    {/if}
                  </span>
                </li>
              {/each}
            </ol>
            {#if status.state === "done"}
              <p class="pg-done">
                <a
                  bind:this={reportLink}
                  class="btn"
                  style="box-shadow: 5px 5px 0 0 #fff"
                  href={`#/report/${id}`}
                >
                  View report →
                </a>
              </p>
            {/if}
            {#if status.state === "failed"}
              <p class="pg-alert pg-failed" role="alert">
                {status.error ?? "Something went wrong on our side. Please try again."}
                <a href="#/">Start again</a>
              </p>
            {/if}
          {:else if failures < 3}
            <div class="pg-skel" aria-label="Loading check status">
              {#each [80, 100, 55, 70] as w, i (w)}
                <div
                  class="pg-skel-bar"
                  style={`width: ${w}%; animation-delay: ${i * 120}ms`}
                ></div>
              {/each}
            </div>
          {/if}
        </div>

        <div class="pg-rail" aria-hidden="true">
          <div class="pg-rail-card">
            <div class="pg-rail-head">
              <span>Building the case</span>
              <span>{builtFloors} / 14</span>
            </div>
            {@render building(builtFloors, 14, null, "dark")}
          </div>
        </div>
      </div>
    </div>
  </section>
{/if}

<style>
  .pg-shell {
    position: relative;
    overflow: hidden;
    background: var(--color-forest);
    color: #fff;
    min-height: 80vh;
  }

  .pg-fill {
    position: absolute;
    inset: 0;
  }

  .pg-inner {
    position: relative;
    max-width: 78rem;
    margin: 0 auto;
    padding: 4rem 1.25rem;
  }

  @media (min-width: 640px) {
    .pg-inner {
      padding: 6rem 2rem;
    }
  }

  .pg-shell .eyebrow {
    color: var(--color-lime);
  }

  .pg-title {
    margin: 1.25rem 0 0;
    font-size: clamp(2.4rem, 5.5vw, 4.6rem);
    line-height: 0.98;
    color: #fff;
  }

  .pg-title-missing {
    margin: 1.25rem 0 0;
    font-size: clamp(2.5rem, 6vw, 5rem);
    line-height: 1;
    color: #fff;
  }

  .pg-lede {
    margin: 1.5rem 0 0;
    max-width: 32rem;
    font-size: 1.125rem;
    color: rgb(255 255 255 / 0.75);
  }

  .pg-cta-row {
    margin: 2.5rem 0 0;
  }

  .pg-grid {
    display: grid;
    gap: 3.5rem;
    align-items: start;
  }

  @media (min-width: 1024px) {
    .pg-grid {
      grid-template-columns: 1.2fr 0.8fr;
    }
  }

  .pg-copy {
    margin-top: 1rem;
  }

  .pg-shell :global(.pg-copy .copy-row) {
    margin-top: 0;
  }

  .pg-shell :global(.pg-copy .meta) {
    color: rgb(255 255 255 / 0.6);
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }

  .pg-shell :global(.pg-copy .ghost-btn) {
    border-color: rgb(255 255 255 / 0.35);
    color: #fff;
  }

  .pg-shell :global(.pg-copy .ghost-btn:hover) {
    background: var(--color-lime);
    color: var(--color-ink);
    border-color: var(--color-lime);
  }

  .pg-alert {
    margin: 2rem 0 0;
    border-radius: 12px;
    border: 2px solid var(--color-flag);
    background: rgb(255 74 43 / 0.15);
    padding: 0.75rem 1rem;
    font-weight: 600;
    color: var(--color-flag-soft);
  }

  .pg-alert a {
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 3px;
    font-weight: 700;
  }

  .pg-score {
    margin-top: 3rem;
    display: flex;
    align-items: flex-end;
    gap: 1rem;
  }

  .pg-num {
    font-size: clamp(5rem, 14vw, 10rem);
    line-height: 0.8;
    color: var(--color-lime);
    font-variant-numeric: tabular-nums;
  }

  .pg-pct {
    padding-bottom: 0.5rem;
    font-size: 2.25rem;
    color: rgb(215 255 68 / 0.7);
  }

  .pg-steps-meta {
    margin-bottom: 0.75rem;
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: rgb(255 255 255 / 0.6);
    text-align: right;
  }

  .pg-track {
    margin-top: 1.5rem;
    height: 1rem;
    overflow: hidden;
    border-radius: 999px;
    border: 2px solid rgb(215 255 68 / 0.6);
    background: rgb(0 0 0 / 0.3);
  }

  .pg-bar {
    position: relative;
    height: 100%;
    border-radius: 999px;
    background: var(--color-lime);
    transition: width 0.6s ease-out;
  }

  .pg-tip {
    position: absolute;
    top: 0;
    bottom: 0;
    right: 0;
    width: 2.5rem;
    background: rgb(255 255 255 / 0.6);
    filter: blur(4px);
    animation: pg-pulse 2s ease-in-out infinite;
  }

  .pg-queue {
    margin: 1rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.875rem;
    color: rgb(255 255 255 / 0.7);
  }

  .pg-steps {
    margin: 3rem 0 0;
    padding: 0;
    list-style: none;
    display: grid;
  }

  .pg-step {
    position: relative;
    display: grid;
    grid-template-columns: 2.4rem 1fr;
    gap: 1rem;
    align-items: start;
    border-top: 1px solid rgb(255 255 255 / 0.12);
    padding: 1rem 0;
    opacity: 0;
    animation: pg-slide 0.45s var(--ease-out) forwards;
  }

  .pg-step.is-pending {
    animation-name: pg-slide-dim;
  }

  .pg-badge {
    display: grid;
    place-items: center;
    height: 2.25rem;
    width: 2.25rem;
    border-radius: 999px;
    border: 2px solid rgb(255 255 255 / 0.3);
    color: rgb(255 255 255 / 0.4);
    font-family: var(--font-mono);
    font-size: 0.875rem;
    font-weight: 700;
  }

  .pg-step.is-done .pg-badge {
    border-color: var(--color-lime);
    background: var(--color-lime);
    color: var(--color-ink);
  }

  .pg-step.is-running .pg-badge {
    border-color: var(--color-lime);
    background: var(--color-forest-3);
    color: var(--color-lime);
    animation: pulse-ring 2.4s ease-out infinite;
  }

  .pg-step.is-failed .pg-badge {
    border-color: var(--color-flag);
    background: var(--color-flag);
    color: #fff;
  }

  .pg-step-name {
    font-size: 1.5rem;
    color: #fff;
  }

  .pg-step.is-running .pg-step-name {
    color: var(--color-lime);
  }

  .pg-step-state {
    margin-left: 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: rgb(255 255 255 / 0.5);
  }

  .pg-step-note {
    display: block;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: rgb(255 255 255 / 0.6);
  }

  .pg-done {
    margin: 2.5rem 0 0;
    animation: pg-rise 0.5s ease-out both;
  }

  .pg-failed {
    margin-top: 2.5rem;
  }

  .pg-skel {
    margin-top: 3rem;
    display: grid;
    gap: 1rem;
  }

  .pg-skel-bar {
    height: 1.25rem;
    border-radius: 999px;
    background: rgb(255 255 255 / 0.1);
    animation: pg-pulse 2s ease-in-out infinite;
  }

  .pg-rail {
    position: relative;
    width: 100%;
    max-width: 24rem;
    margin-inline: auto;
  }

  @media (min-width: 1024px) {
    .pg-rail {
      position: sticky;
      top: 2rem;
    }
  }

  .pg-rail-card {
    border-radius: 28px;
    border: 1px solid rgb(215 255 68 / 0.3);
    background: rgb(14 58 41 / 0.7);
    padding: 1.5rem;
    backdrop-filter: blur(8px);
  }

  .pg-rail-head {
    display: flex;
    justify-content: space-between;
    margin-bottom: 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: rgb(215 255 68 / 0.8);
  }

  .pg-building {
    width: 100%;
    height: auto;
    display: block;
  }

  @keyframes pg-slide {
    from {
      opacity: 0;
      transform: translateX(-12px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  @keyframes pg-slide-dim {
    from {
      opacity: 0;
      transform: translateX(-12px);
    }
    to {
      opacity: 0.4;
      transform: none;
    }
  }

  @keyframes pg-rise {
    from {
      opacity: 0;
      transform: translateY(16px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  @keyframes pg-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.4;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .pg-step {
      opacity: 1;
      animation: none;
    }

    .pg-step.is-pending {
      opacity: 0.4;
    }

    .pg-step.is-running .pg-badge,
    .pg-tip,
    .pg-skel-bar {
      animation: none;
    }

    .pg-done {
      animation: none;
    }

    .pg-bar {
      transition: none;
    }
  }
</style>
