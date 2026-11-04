<script lang="ts">
  import { formatDate } from "../lib/format";
  import { mediaUrl } from "../lib/api";

  let {
    checkId,
    imageId,
    overlayPath,
    quarterEnd,
    rowsMarked,
  }: {
    checkId: string;
    imageId: string;
    overlayPath: string | null;
    quarterEnd: string | null;
    rowsMarked: number | null;
  } = $props();

  let showOverlay = $state(true);
  const rowsText = $derived(
    rowsMarked === null ? "rows unknown" : `${rowsMarked} rows marked`,
  );

  // Only *.overlay.png + *.overlay.thumb.jpg are served from /media; a bare
  // image id (no image extension) can never load, so never request it.
  const canShowRaw = $derived(
    /\.(png|jpe?g|webp)$/i.test(imageId.split("/").pop() ?? ""),
  );
  const srcName = $derived(
    showOverlay && overlayPath
      ? overlayPath
      : !showOverlay && canShowRaw
        ? imageId
        : (overlayPath ?? (canShowRaw ? imageId : null)),
  );
  let failedSrc = $state<string | null>(null);
  const imgOk = $derived(srcName !== null && failedSrc !== srcName);
  const showingOverlay = $derived(imgOk && srcName === overlayPath);
</script>

<figure class="photo">
  <figcaption class="photo-bar">
    <span>
      Quarter ending {formatDate(quarterEnd)} · floor-count overlay ({rowsText})
    </span>
    <label class="toggle">
      <input
        type="checkbox"
        bind:checked={showOverlay}
        disabled={overlayPath === null || !canShowRaw}
        title={overlayPath !== null && !canShowRaw
          ? "Only the overlay is available for this photo"
          : undefined}
      />
      Show overlay
    </label>
  </figcaption>
  {#if imgOk && srcName}
    <img
      class="photo-img"
      src={mediaUrl(checkId, srcName)}
      onerror={() => (failedSrc = srcName)}
      alt={`Elevation photo, quarter ending ${formatDate(quarterEnd)}, ${rowsText}`}
      loading="lazy"
    />
  {:else}
    <p class="photo-missing">The overlay image for this photo is not available.</p>
  {/if}
  {#if showingOverlay}
    <div class="legend" aria-hidden="true">
      <span class="swatch"></span> detected opening row
    </div>
  {/if}
</figure>

<style>
  /* Caption bar + footer of the reference overlay figure (ref PhotoEvidence.tsx:46-72). */
  .photo {
    overflow: hidden;
    border-radius: 16px;
    border: 2px solid var(--color-ink);
    background: var(--color-card);
  }

  .photo-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    border-bottom: 2px solid var(--color-ink);
    background: var(--color-paper);
    padding: 0.75rem 1rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
  }

  .photo-bar .toggle {
    min-height: 2.25rem;
    font-weight: 700;
  }

  .photo-img {
    display: block;
    border: 0;
    border-radius: 0;
    background: var(--color-card);
  }

  .photo-missing {
    margin: 0;
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--color-muted);
    background: var(--color-paper);
  }

  .photo .legend {
    margin-top: 0;
    padding: 0.5rem 1rem;
  }

  .photo .swatch {
    background: var(--color-lime);
    outline: 1px solid var(--color-ink);
  }
</style>
