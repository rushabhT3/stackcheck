<script lang="ts">
  import type { Finding } from "../lib/types";
  import StatusTag from "./StatusTag.svelte";
  import OverlayThumb from "./OverlayThumb.svelte";
  import type { PhotoMeta } from "./FindingCard.svelte";

  let {
    findings,
    checkId,
    photos,
  }: {
    findings: Finding[];
    checkId: string;
    photos: PhotoMeta[];
  } = $props();

  const items = $derived(findings.filter((f) => f.status === "CANT_VERIFY"));

  // Collapsed state is memory-only; expanded by default.
  let collapsed = $state(false);
</script>

<section aria-labelledby="cant-verify-heading">
  <h2 id="cant-verify-heading">
    <button
      type="button"
      class="cant-toggle"
      aria-expanded={!collapsed}
      aria-controls="cant-verify-body"
      onclick={() => (collapsed = !collapsed)}
    >
      <span>What could not be verified <span class="cant-count">({items.length})</span></span>
      <span class="cant-arrow" class:cant-collapsed={collapsed} aria-hidden="true">▾</span>
    </button>
  </h2>
  {#if !collapsed}
    <div id="cant-verify-body">
      {#if items.length === 0}
        <p class="cant-empty">Everything in this check was confirmed or flagged.</p>
      {:else}
        <ul class="plain">
          {#each items as f (f.finding_id)}
            {@const photo = f.evidence.find((ev) => ev.kind === "photo" && ev.image_id)}
            {@const meta = photo ? photos.find((p) => p.imageId === photo.image_id) : undefined}
            {@const overlay = meta?.overlayPath ?? photo?.overlay_path ?? null}
            {@const rows = meta?.rowsMarked ?? null}
            <li>
              <StatusTag status={f.status} />
              <p class="cant-title"><strong>{f.title}</strong></p>
              <p class="cant-explain">{f.explanation}</p>
              {#if photo && overlay}
                <OverlayThumb
                  {checkId}
                  overlayPath={overlay}
                  alt={rows === null ? "Elevation photo" : `Elevation photo with ${rows} counted floor rows`}
                />
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</section>

<style>
  /* Collapse toggle: resets the global lime-pill button so the title
   * keeps its exact styling; only the arrow is added. */
  .cant-toggle {
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

  .cant-toggle:hover:not(:disabled),
  .cant-toggle:active:not(:disabled) {
    transform: none;
    box-shadow: none;
    background: none;
  }

  .cant-arrow {
    display: inline-block;
    font-size: 0.6em;
    line-height: 1;
    color: var(--color-forest-3);
    transition: transform 160ms ease;
  }

  .cant-arrow.cant-collapsed {
    transform: rotate(-90deg);
  }

  .cant-count {
    color: var(--color-muted);
  }

  .cant-empty {
    margin-top: 1rem;
    color: rgb(11 21 16 / 0.7);
  }

  .cant-title {
    margin: 0.75rem 0 0;
  }

  .cant-explain {
    margin: 0.25rem 0 0;
    color: rgb(11 21 16 / 0.75);
  }
</style>
