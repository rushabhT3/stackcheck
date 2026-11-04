<script lang="ts">
  import type { Verdict } from "../lib/types";
  import CostBadge from "./CostBadge.svelte";
  import type { CostSummary } from "../lib/types";

  let {
    verdict,
    costs,
    countOnly = false,
  }: { verdict: Verdict; costs: CostSummary; countOnly?: boolean } = $props();

  const hasFlags = $derived(
    verdict.flags_high + verdict.flags_medium + verdict.flags_low > 0,
  );
</script>

<header class="banner {hasFlags ? 'is-flag' : 'is-ok'}">
  <span class="vb-eyebrow {hasFlags ? 'is-flag' : 'is-ok'}">
    <span class="vb-dot" aria-hidden="true"></span>
    {countOnly ? "COUNT ONLY" : hasFlags ? "Verdict · gaps found" : "Verdict · lines up"}
  </span>
  <h1 class="display">{verdict.headline}</h1>
  {#if !countOnly}
    <ul class="counts">
      <li><strong>{verdict.flags_high}</strong> <span class="vb-label">high-priority flags</span></li>
      <li><strong>{verdict.flags_medium}</strong> <span class="vb-label">medium</span></li>
      <li><strong>{verdict.flags_low}</strong> <span class="vb-label">low</span></li>
      <li><strong>{verdict.cant_verify}</strong> <span class="vb-label">can't verify</span></li>
      <li><strong>{verdict.ok}</strong> <span class="vb-label">confirmed</span></li>
    </ul>
  {/if}
  <CostBadge {costs} />
</header>

<style>
  /* Eyebrow verdict pill + stat label dots (ref ReportParts.tsx:25-50). */
  .vb-eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    border-radius: 999px;
    border: 2px solid var(--color-lime);
    color: var(--color-lime);
    padding: 0.375rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .vb-eyebrow.is-flag {
    border-color: var(--color-flag);
    color: var(--color-flag-soft);
  }

  .vb-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--color-lime);
    flex: none;
  }

  .vb-eyebrow.is-flag .vb-dot {
    background: var(--color-flag);
  }

  .banner .display {
    margin-top: 1.5rem;
  }

  .vb-label {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
  }

  .vb-label::before {
    content: "";
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--color-flag);
    flex: none;
  }

  .counts li:nth-child(2) .vb-label::before {
    background: var(--color-medium);
  }

  .counts li:nth-child(3) .vb-label::before {
    background: var(--color-amber);
  }

  .counts li:nth-child(4) .vb-label::before {
    background: rgb(255 255 255 / 0.5);
  }

  .counts li:nth-child(5) .vb-label::before {
    background: var(--color-lime);
  }
</style>
