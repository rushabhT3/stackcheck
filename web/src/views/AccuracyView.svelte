<script lang="ts">
  import { getEval, userMessage } from "../lib/api";
  import type { EvalReport, EvalValue } from "../lib/types";
  import { formatDate } from "../lib/format";

  let evalReport = $state<EvalReport | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    document.title = "How accurate is this | StackCheck";
    let alive = true;
    getEval()
      .then((e) => {
        if (alive) evalReport = e;
      })
      .catch((e: unknown) => {
        if (alive) error = userMessage(e);
      });
    return () => {
      alive = false;
    };
  });

  function showShare(metric: EvalValue): string {
    if (metric.value === null) return "Not measured yet";
    return `${(metric.value * 100).toFixed(1)}%`;
  }

  function showCount(metric: EvalValue): string {
    if (metric.value === null) return "not measured yet";
    return `${Math.round(metric.value * metric.n)} of ${metric.n}`;
  }
</script>

{#snippet metric(title: string, m: EvalValue, sub: string | null, invert: boolean)}
  <div class="metric">
    <span class="eyebrow metric-title">{title}</span>
    <div class="metric-num display" class:is-none={m.value === null}>{showShare(m)}</div>
    <div class="metric-bar" aria-hidden="true">
      <div
        class="metric-fill"
        class:good={m.value === null ? false : invert ? m.value * 100 < 10 : m.value * 100 >= 70}
        class:has-value={(m.value ?? 0) !== 0}
        style={`width: ${(m.value ?? 0) * 100}%`}
      ></div>
    </div>
    <p class="metric-sub">{sub ?? showCount(m)}</p>
    <p class="metric-method">{m.method}</p>
  </div>
{/snippet}

{#snippet tierSeg(name: string, mv: EvalValue, cls: string)}
  <div class={`tier-seg ${cls}`} style={`width: ${(mv.value ?? 0) * 100}%`}>
    <span class="tier-label">{name} {showCount(mv)}</span>
  </div>
{/snippet}

<section class="acc-hero on-dark noise" aria-labelledby="accuracy-heading">
  <div class="grid-bg acc-grid-bg" aria-hidden="true"></div>
  <div class="wrap acc-hero-inner">
    <span class="eyebrow">Evidence, not promises</span>
    <h1 id="accuracy-heading">How accurate <em>is this?</em></h1>
    <p class="acc-lede">
      Plain numbers: what we checked, on how many cases, how we checked it, and
      when. Where a number is missing, this page says so instead of guessing.
    </p>
    {#if evalReport?.date}
      <p class="acc-date-pill">Measured on {formatDate(evalReport.date)}</p>
    {/if}
  </div>
</section>

<div class="wrap acc-body">
  {#if error}
    <p class="acc-error" role="alert">{error}</p>
  {:else if evalReport === null}
    <p class="acc-loading">Loading…</p>
  {:else if evalReport.measured === false || evalReport.date === null}
    <div class="acc-notyet">
      <p class="display acc-notyet-title">Not measured yet.</p>
      <p class="acc-notyet-text">
        We have not run the accuracy check for this copy of the site yet. When we
        do, this page will show plain numbers.
      </p>
    </div>
  {:else}
    <section class="acc-section" aria-labelledby="acc-floors">
      <div class="acc-sec-head">
        <span class="eyebrow">01 · Photos</span>
        <h2 id="acc-floors">Floor counts on photos</h2>
      </div>
      <p class="acc-note">{evalReport.floors?.reference?.note ?? "Floor counts were read from each photo by Claude, an AI model used as a second reader. A person has not checked them yet."}</p>
      {#if !evalReport.floors || evalReport.floors.status !== "measured"}
        <p class="notyet">Not measured yet. {evalReport.floors?.reason ?? ""}</p>
      {:else}
        {@const floors = evalReport.floors}
        {@const abstained = Math.round((floors.abstain.value ?? 0) * floors.abstain.n)}
        {#if floors.tiers}
          {@const tiers = floors.tiers}
          <div class="tiers" role="img" aria-label="Confidence tiers">
            {@render tierSeg("Confident", tiers.confident, "seg-conf")}
            {@render tierSeg("Estimate", tiers.estimate, "seg-est")}
            {@render tierSeg("Can't tell", tiers.cant_tell, "seg-cant")}
          </div>
        {/if}
        <div class="grid-metrics">
          {@render metric("Exact floor count", floors.exact, null, false)}
          {@render metric("Within one floor", floors.within1, null, false)}
          {@render metric("Abstained", floors.abstain, `${abstained} of ${floors.abstain.n} abstained`, false)}
          {@render metric("Wrong when confident", floors.wrong_when_confident, `${showCount(floors.wrong_when_confident)} off by 2+ floors`, true)}
        </div>
        <p class="acc-body-text">
          On {abstained} of {floors.abstain.n} photos the tool said it can't tell.
          <strong>That is by design.</strong>
        </p>
        {#if floors.disagreements && floors.disagreements.value !== null}
          <p class="acc-body-text">
            {Math.round(floors.disagreements.value * floors.disagreements.n)} of {floors.disagreements.n}
            compared photos where the person and the AI reader disagree.
          </p>
        {/if}
        {#if evalReport.floors_video?.status === "measured" && evalReport.floors_video.within1.value !== null}
          <div class="video-panel">
            <p class="display video-title">
              Video: {showCount(evalReport.floors_video.within1)} within one floor of the reference count.
            </p>
            <p class="video-sub">References come from public records, not a person recount.</p>
            {#if evalReport.floors_video.videos}
              <ul class="video-list">
                {#each evalReport.floors_video.videos as v (v.id)}
                  <li>
                    <strong>{v.id}</strong>: off by {v.off_by} {v.off_by === 1 ? "floor" : "floors"}
                    (bench {v.bench_low}{v.bench_low === v.bench_high ? "" : ` to ${v.bench_high}`},
                    public record {Array.isArray(v.reference) ? `${v.reference[0]} to ${v.reference[1]}` : v.reference}{#if v.source}
                      · {v.source}{/if})
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}
      {/if}
    </section>

    <section class="acc-section" aria-labelledby="acc-extraction">
      <div class="acc-sec-head">
        <span class="eyebrow">02 · Documents</span>
        <h2 id="acc-extraction">Reading the filings</h2>
      </div>
      {#if !evalReport.extraction || evalReport.extraction.status !== "measured"}
        <p class="notyet">Not measured yet. {evalReport.extraction?.reason ?? ""}</p>
      {:else}
        <div class="grid-metrics">
          {#each Object.entries(evalReport.extraction.fields) as [field, m] (field)}
            {@render metric(field.replace(/_/g, " "), m, null, false)}
          {/each}
        </div>
      {/if}
      {#if evalReport.golden && evalReport.golden.status === "measured"}
        <div class="golden-panel on-dark">
          <span class="eyebrow">Project E golden check</span>
          <p class="display golden-title">
            {evalReport.golden.activities_matched} of {evalReport.golden.activities_total} activity rows and
            {evalReport.golden.money_matched} of {evalReport.golden.money_total} money values converge.
          </p>
          <p class="golden-gap">
            Known gap, not hidden: {evalReport.golden.known_gap}. Rules: R4 {evalReport.golden.rule_pins.r4}, R7 {evalReport.golden.rule_pins.r7}.
          </p>
          <p class="golden-method">{evalReport.golden.method}</p>
        </div>
      {/if}
    </section>

    <section class="acc-section" aria-labelledby="acc-flags">
      <div class="acc-sec-head">
        <span class="eyebrow">03 · Rules</span>
        <h2 id="acc-flags">Flag precision by rule</h2>
      </div>
      {#if !evalReport.flags || evalReport.flags.status !== "measured"}
        <p class="notyet">Not measured yet. {evalReport.flags?.reason ?? ""}</p>
      {:else}
        {#if evalReport.flags.reviewer_note}
          <p class="reviewer-note">
            {evalReport.flags.reviewer_note}{" "}{#if evalReport.flags.reviewed_on}
              Reviewed on {formatDate(evalReport.flags.reviewed_on)}.{/if}
          </p>
        {/if}
        <div class="grid-flags">
          {#each Object.entries(evalReport.flags.rules) as [rule, r] (rule)}
            {@render metric(rule, r.precision, `${r.true} right · ${r.false} wrong · ${r.unclear} unclear`, false)}
          {/each}
        </div>
      {/if}
    </section>

    <section class="acc-section" aria-labelledby="acc-backtest">
      <div class="acc-sec-head">
        <span class="eyebrow">04 · Reality</span>
        <h2 id="acc-backtest">Backtest against regulator action</h2>
      </div>
      {#if !evalReport.backtest || evalReport.backtest.status !== "measured"}
        <p class="notyet">Not measured yet. {evalReport.backtest?.reason ?? ""}</p>
      {:else}
        <div class="grid-backtest">
          {#each evalReport.backtest.projects as p (p.project_label + p.action_date)}
            <div class="backtest-card">
              <span class="eyebrow" class:is-flagged={p.status === "flagged"}>
                {p.status === "flagged" ? "● flagged early" : "○ not flagged"}
              </span>
              <strong class="display backtest-title">{p.project_label}</strong>
              <p class="acc-body-text">
                {#if p.status === "flagged" && p.earliest_flag !== null}
                  First flag {p.earliest_flag}, {p.quarters_before.value ?? "unknown"} quarters before the {p.action_type} on {p.action_date}.
                {:else}
                  Not flagged before the {p.action_type} on {p.action_date}.
                {/if}
              </p>
              <p class="metric-method">{p.quarters_before.method}</p>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <p class="acc-closing">
      A gap StackCheck reports is a question to ask the builder or RERA,
      <strong>not proof of wrongdoing</strong>. Where the evidence is unclear,
      the report says it can't verify.
    </p>
  {/if}
</div>

<style>
  .acc-hero {
    position: relative;
    overflow: hidden;
    background: var(--color-forest);
    color: #fff;
  }

  .acc-grid-bg {
    position: absolute;
    inset: 0;
  }

  .acc-hero-inner {
    position: relative;
    z-index: 1;
    padding-top: 5rem;
    padding-bottom: 5rem;
  }

  .acc-hero .eyebrow {
    color: var(--color-lime);
    margin-bottom: 0;
  }

  .acc-hero h1 {
    margin: 1.5rem 0 0;
    max-width: 56rem;
    font-size: clamp(2.8rem, 7vw, 6rem);
    line-height: 0.94;
    color: #fff;
  }

  .acc-hero h1 em {
    font-style: italic;
    color: var(--color-lime);
  }

  .acc-lede {
    margin: 2rem 0 0;
    max-width: 42rem;
    font-size: 1.125rem;
    color: rgb(255 255 255 / 0.75);
  }

  .acc-date-pill {
    display: inline-flex;
    margin: 1.5rem 0 0;
    padding: 0.375rem 1rem;
    border: 1px solid rgb(215 255 68 / 0.5);
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-lime);
  }

  .acc-body {
    padding-bottom: 2rem;
  }

  .acc-error {
    margin-top: 3rem;
    font-weight: 600;
    color: var(--color-flag);
  }

  .acc-loading {
    margin-top: 3rem;
    font-family: var(--font-mono);
    font-size: 0.875rem;
  }

  .acc-notyet {
    margin-top: 3rem;
    max-width: 42rem;
  }

  .acc-notyet-title {
    margin: 0;
    font-size: 2.5rem;
    line-height: 1;
  }

  .acc-notyet-text {
    margin: 1rem 0 0;
    font-size: 1.125rem;
    color: rgb(11 21 16 / 0.75);
  }

  .acc-section {
    padding-top: 5rem;
  }

  .acc-sec-head {
    margin-bottom: 2rem;
    padding-bottom: 1rem;
    border-bottom: 2px solid var(--color-ink);
  }

  .acc-sec-head .eyebrow {
    margin-bottom: 0;
  }

  .acc-sec-head h2 {
    margin: 0.5rem 0 0;
    font-size: clamp(1.9rem, 3.6vw, 3rem);
    line-height: 1;
  }

  .acc-note {
    margin: 0 0 1.5rem;
    max-width: 48rem;
    color: var(--color-muted);
  }

  .notyet {
    margin: 0;
    padding: 1.5rem;
    border: 2px dashed rgb(11 21 16 / 0.4);
    border-radius: 16px;
  }

  .tiers {
    display: flex;
    height: 3rem;
    margin-bottom: 1.5rem;
    overflow: hidden;
    border: 2px solid var(--color-ink);
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
  }

  .tier-seg {
    display: flex;
    align-items: center;
    justify-content: center;
    border-right: 2px solid var(--color-ink);
  }

  .tier-seg:last-child {
    border-right: 0;
  }

  .seg-conf {
    background: var(--color-lime);
  }

  .seg-est {
    background: var(--color-amber);
  }

  .seg-cant {
    background: var(--color-paper-2);
  }

  .tier-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 0 0.5rem;
  }

  .grid-metrics,
  .grid-flags,
  .grid-backtest {
    display: grid;
    grid-template-columns: 1fr;
    gap: 1.25rem;
  }

  .metric,
  .backtest-card {
    animation: rise 0.7s var(--ease-out) both;
  }

  .metric {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: 1.5rem;
    border: 2px solid var(--color-ink);
    border-radius: 22px;
    background: var(--color-card);
    transition: transform 160ms ease, box-shadow 160ms ease;
  }

  .metric:hover {
    transform: translateY(-4px);
    box-shadow: 6px 6px 0 0 var(--color-ink);
  }

  .metric-title {
    color: var(--color-muted);
    margin-bottom: 0;
  }

  .metric-num {
    margin-top: 0.75rem;
    font-size: clamp(3rem, 6vw, 4.4rem);
    line-height: 1;
  }

  .metric-num.is-none {
    font-size: 1.875rem;
    color: var(--color-muted);
  }

  .metric-bar {
    height: 0.75rem;
    margin-top: 1rem;
    overflow: hidden;
    border: 2px solid var(--color-ink);
    border-radius: 999px;
    background: var(--color-paper);
  }

  .metric-fill {
    height: 100%;
    background: var(--color-amber);
  }

  .metric-fill.good {
    background: var(--color-lime);
  }

  .metric-fill.has-value {
    border-right: 2px solid var(--color-ink);
  }

  .metric-sub {
    margin: 0.75rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    font-weight: 700;
  }

  .metric-method {
    margin: 0.25rem 0 0;
    flex: 1;
    font-size: 0.875rem;
    color: var(--color-muted);
  }

  .acc-body-text {
    color: rgb(11 21 16 / 0.8);
  }

  .video-panel {
    margin-top: 2rem;
    padding: 1.5rem;
    border: 2px solid var(--color-ink);
    border-radius: 22px;
    background: var(--color-card);
  }

  .video-title {
    margin: 0;
    font-size: 1.5rem;
    line-height: 1.2;
  }

  .video-sub {
    margin: 0.25rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .video-list {
    display: grid;
    gap: 0.25rem;
    list-style: none;
    margin: 1rem 0 0;
    padding: 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }

  .golden-panel {
    margin-top: 2rem;
    padding: 1.5rem;
    border: 2px solid var(--color-ink);
    border-radius: 22px;
    background: var(--color-ink);
    color: #fff;
  }

  .golden-panel .eyebrow {
    color: var(--color-lime);
    margin-bottom: 0;
  }

  .golden-title {
    margin: 0.5rem 0 0;
    font-size: 1.5rem;
    line-height: 1.35;
  }

  .golden-gap {
    margin: 0.5rem 0 0;
    color: rgb(255 255 255 / 0.8);
  }

  .golden-method {
    margin: 0.5rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: rgb(255 255 255 / 0.5);
  }

  .reviewer-note {
    margin: 0 0 1.5rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
  }

  .backtest-card {
    height: 100%;
    padding: 1.5rem;
    border: 2px solid var(--color-ink);
    border-radius: 22px;
    background: var(--color-card);
  }

  .backtest-card .eyebrow {
    color: var(--color-muted);
    margin-bottom: 0;
  }

  .backtest-card .eyebrow.is-flagged {
    color: var(--color-ok);
  }

  .backtest-title {
    display: block;
    margin-top: 0.5rem;
    font-size: 1.5rem;
    font-weight: 400;
  }

  .acc-closing {
    margin: 5rem 0 0;
    max-width: 48rem;
    padding-left: 1.25rem;
    border-left: 4px solid var(--color-lime);
    font-size: 1.125rem;
    color: rgb(11 21 16 / 0.8);
  }

  @media (min-width: 40rem) {
    .acc-hero-inner {
      padding-top: 7rem;
      padding-bottom: 7rem;
    }

    .grid-metrics {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (min-width: 48rem) {
    .grid-flags {
      grid-template-columns: repeat(3, 1fr);
    }

    .grid-backtest {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (min-width: 64rem) {
    .grid-metrics {
      grid-template-columns: repeat(4, 1fr);
    }
  }
</style>
