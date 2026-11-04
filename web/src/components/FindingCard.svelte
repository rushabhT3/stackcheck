<script lang="ts">
  import type { Evidence, Finding } from "../lib/types";
  import StatusTag from "./StatusTag.svelte";
  import PhotoEvidence from "./PhotoEvidence.svelte";
  import { mediaUrl } from "../lib/api";

  export interface PhotoMeta {
    imageId: string;
    overlayPath: string | null;
    quarterEnd: string | null;
    rowsMarked: number | null;
  }

  let {
    finding,
    checkId,
    photos,
    docUrls,
  }: {
    finding: Finding;
    checkId: string;
    photos: PhotoMeta[];
    docUrls: Record<string, string | null>;
  } = $props();

  let expanded = $state<Record<string, boolean>>({});
  let thumbFailed = $state<Record<string, boolean>>({});
  let fullFailed = $state<Record<string, boolean>>({});

  function photoFor(ev: Evidence): PhotoMeta | undefined {
    return photos.find((p) => p.imageId === ev.image_id);
  }

  /** Thumb next to the full overlay, derived by convention so old reports still work. */
  function thumbFor(path: string | null): string | null {
    if (!path || !path.endsWith(".overlay.png")) return null;
    return `${path.slice(0, -".overlay.png".length)}.overlay.thumb.jpg`;
  }

  function pageUrl(ev: Evidence): string | null {
    if (!ev.ref) return null;
    return docUrls[ev.ref.doc_id] ?? null;
  }

  const r2photos = $derived(
    finding.evidence.filter((ev) => ev.kind === "photo" && ev.image_id),
  );
  const isR2Pair = $derived(finding.rule === "R2_REUSED_PHOTO" && r2photos.length >= 2);
</script>

{#snippet pagelink(ev: Evidence)}
  {#if ev.ref}
    {@const href = pageUrl(ev)}
    {#if href}
      <a
        class="page-pill"
        href={`${href}#page=${ev.ref.page}`}
        target="_blank"
        rel="noopener noreferrer">page {ev.ref.page} ↗</a
      >
    {:else}
      <span class="page-pill">page {ev.ref.page}</span>
    {/if}
  {/if}
{/snippet}

<article class="card">
  <div class="card-head">
    <StatusTag status={finding.status} />
    <span class="rule">{finding.rule} · {finding.severity}</span>
  </div>
  <h3>{finding.title}</h3>
  <p class="explain">{finding.explanation}</p>
  {#if finding.inputs.floors_entered_by_you}
    <p class="meta">Floors entered by you: {finding.inputs.floors_entered_by_you}</p>
  {/if}
  {#if finding.block_label}
    <p class="meta">Block: {finding.block_label}</p>
  {/if}
  {#if isR2Pair}
    <div class="r2-pair">
      {#each r2photos as ev, i (ev.image_id)}
        {@const meta = photoFor(ev)}
        {@const full = meta?.overlayPath ?? ev.overlay_path}
        {@const thumb = thumbFor(full)}
        {@const key = ev.image_id ?? ""}
        <figure class="r2-photo">
          {#if thumb && !thumbFailed[key]}
            <img
              class="photo-thumb"
              src={mediaUrl(checkId, thumb)}
              onerror={() => (thumbFailed[key] = true)}
              alt="Reused overlay thumbnail"
              loading="lazy"
            />
          {:else if full && !fullFailed[key]}
            <img
              class="photo-thumb"
              src={mediaUrl(checkId, full)}
              onerror={() => (fullFailed[key] = true)}
              alt="Reused overlay thumbnail"
              loading="lazy"
            />
          {:else if full}
            <span class="photo-note">Overlay image not available.</span>
          {/if}
          <figcaption>
            {#if i < 2}<span class="r2-seq">{i === 0 ? "Earlier" : "Later"}</span>{/if}
            {ev.note}
            {@render pagelink(ev)}
          </figcaption>
        </figure>
      {/each}
    </div>
  {/if}
  {#if finding.evidence.length > 0}
    <div class="ev-sec">
      <span class="eyebrow">Evidence</span>
      <ul class="evidence">
        {#each finding.evidence as ev, i (i)}
          <li>
            {#if ev.kind === "quote" && ev.quote}
              <blockquote>
                <p class="quote-text">“{ev.quote}”</p>
                <p class="quote-meta">
                  {ev.note}
                  {@render pagelink(ev)}
                </p>
              </blockquote>
            {:else if ev.kind === "photo" && ev.image_id && !isR2Pair}
              {@const meta = photoFor(ev)}
              {@const full = meta?.overlayPath ?? ev.overlay_path}
              {@const thumb = thumbFor(full)}
              {@const key = ev.image_id ?? ""}
              {@const rows = meta?.rowsMarked ?? null}
              {@const alt = rows === null ? "Elevation photo" : `Elevation photo with ${rows} counted floor rows`}
              <div class="photo-row">
                {#if thumb && !thumbFailed[key]}
                  <img
                    class="photo-thumb"
                    src={mediaUrl(checkId, thumb)}
                    onerror={() => (thumbFailed[key] = true)}
                    {alt}
                    loading="lazy"
                  />
                {:else if full && !fullFailed[key]}
                  <img
                    class="photo-thumb"
                    src={mediaUrl(checkId, full)}
                    onerror={() => (fullFailed[key] = true)}
                    {alt}
                    loading="lazy"
                  />
                {:else if full}
                  <span class="photo-note">Overlay image not available.</span>
                {/if}
                <div class="photo-side">
                  {#if ev.note}<span class="photo-note">{ev.note}</span>{/if}
                  <div class="photo-actions">
                    <button
                      type="button"
                      class="btn btn-ghost"
                      onclick={() => (expanded[key] = !expanded[key])}
                    >
                      {expanded[key] ? "Hide photo" : "View photo"}
                    </button>
                    {@render pagelink(ev)}
                  </div>
                </div>
              </div>
              {#if expanded[ev.image_id ?? ""]}
                {#if meta}
                  <PhotoEvidence
                    {checkId}
                    imageId={meta.imageId}
                    overlayPath={meta.overlayPath ?? ev.overlay_path}
                    quarterEnd={meta.quarterEnd}
                    rowsMarked={meta.rowsMarked}
                  />
                {:else}
                  <p class="meta">Photo {ev.image_id} is not in this report.</p>
                {/if}
              {/if}
            {:else if ev.kind === "photo" && ev.image_id && isR2Pair}
              <p class="meta">{ev.note}</p>
            {:else}
              <div class="compute">
                <p class="compute-value">
                  <span class="compute-fn" aria-hidden="true">ƒ</span>
                  {ev.value ?? "No value"}
                  {@render pagelink(ev)}
                </p>
                {#if ev.note}
                  <p class="compute-note">{ev.note}</p>
                {/if}
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</article>

<style>
  /* Reference FindingCard language (ref FindingCard.tsx:26-134): page pills,
   * R2 sequence tags, evidence section, quote meta, photo side column,
   * and the dark computation panel. */
  .page-pill {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    border: 2px solid var(--color-ink);
    background: var(--color-card);
    padding: 0.125rem 0.625rem;
    margin-left: 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    font-weight: 700;
    font-style: normal;
    text-decoration: none;
    white-space: nowrap;
    color: var(--color-ink);
    transition: background 160ms ease;
  }

  a.page-pill:hover {
    background: var(--color-lime);
    text-decoration: none;
    color: var(--color-ink);
  }

  .r2-seq {
    margin-right: 0.25rem;
    font-family: var(--font-mono);
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-flag);
  }

  .ev-sec {
    margin: 1.5rem 0 0;
    padding-top: 1.25rem;
    border-top: 2px dashed rgb(11 21 16 / 0.2);
  }

  .ev-sec .eyebrow {
    color: var(--color-muted);
  }

  .ev-sec .evidence {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }

  .quote-text {
    margin: 0;
  }

  .quote-meta {
    margin: 0.5rem 0 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.5rem;
    font-family: var(--font-mono);
    font-style: normal;
    font-size: 0.72rem;
    color: var(--color-muted);
  }

  .quote-meta .page-pill {
    margin-left: 0;
  }

  .photo-side {
    display: grid;
    gap: 0.5rem;
    min-width: 0;
  }

  .photo-note {
    font-size: 0.9rem;
  }

  .photo-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
  }

  .photo-actions .page-pill,
  .compute-value .page-pill {
    margin-left: 0;
  }

  .compute {
    border-radius: 12px;
    border: 2px solid var(--color-ink);
    background: var(--color-ink);
    padding: 0.75rem 1rem;
    color: #fff;
  }

  .compute-value {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.5rem;
    font-family: var(--font-mono);
    font-size: 0.92rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--color-lime);
  }

  .compute-fn {
    color: rgb(255 255 255 / 0.4);
  }

  .compute-note {
    margin: 0.25rem 0 0;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    color: rgb(255 255 255 / 0.6);
  }
</style>
