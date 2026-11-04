<script lang="ts">
  let { url }: { url: string } = $props();

  let copied = $state(false);
  let timer: number | null = null;

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      const area = document.createElement("textarea");
      area.value = url;
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } catch {
        // Clipboard unavailable: the visible URL is still selectable.
      }
      area.remove();
    }
    copied = true;
    if (timer !== null) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      copied = false;
    }, 2000);
  }
</script>

<div class="copy-row">
  <button type="button" class="ghost-btn copy-btn" onclick={() => void copy()}>
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2.4"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
    </svg>
    Copy link
  </button>
  <span class="meta" role="status">
    {copied
      ? "✓ Link copied"
      : "Bookmark this page or copy the link. Recent checks also appear on the home page of this browser."}
  </span>
</div>

<style>
  /* Icon + label sit inline like the reference CopyLink button (ref ui.tsx:75-81). */
  .copy-row .copy-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
  }
</style>
