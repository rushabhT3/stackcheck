<script lang="ts">
  import type { CostSummary } from "../lib/types";

  let { costs }: { costs: CostSummary } = $props();

  const dollars = $derived(Number(costs.usd_total));
</script>

<p class="cost">
  Model spend {Number.isFinite(dollars) ? `$${dollars.toFixed(4)}` : "unknown"} ·
  {costs.calls} call{costs.calls === 1 ? "" : "s"} ·
  {costs.cache_hits} cache hit{costs.cache_hits === 1 ? "" : "s"}
</p>
