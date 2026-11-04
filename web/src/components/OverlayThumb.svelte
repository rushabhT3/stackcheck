<script lang="ts">
  import { mediaUrl } from "../lib/api";

  let {
    checkId,
    overlayPath,
    alt,
  }: {
    checkId: string;
    overlayPath: string | null;
    alt: string;
  } = $props();

  /** Thumb next to the full overlay, derived by convention so old reports still work. */
  function thumbFor(path: string | null): string | null {
    if (!path || !path.endsWith(".overlay.png")) return null;
    return `${path.slice(0, -".overlay.png".length)}.overlay.thumb.jpg`;
  }

  let thumbFailed = $state(false);
  let fullFailed = $state(false);
  const thumb = $derived(thumbFor(overlayPath));
</script>

{#if thumb && !thumbFailed}
  <img
    class="photo-thumb"
    src={mediaUrl(checkId, thumb)}
    onerror={() => (thumbFailed = true)}
    {alt}
    loading="lazy"
  />
{:else if overlayPath && !fullFailed}
  <img
    class="photo-thumb"
    src={mediaUrl(checkId, overlayPath)}
    onerror={() => (fullFailed = true)}
    {alt}
    loading="lazy"
  />
{/if}
