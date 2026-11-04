<script lang="ts">
  import { statusLabel } from "../lib/format";

  let { status }: { status: string } = $props();

  const icon = $derived(status === "FLAG" ? "✕" : status === "OK" ? "✓" : "?");
  const tone = $derived(status === "FLAG" ? "flag" : status === "OK" ? "ok" : "unsure");
</script>

<span class="tag {tone}"><span aria-hidden="true">{icon}</span> {statusLabel(status)}</span>
