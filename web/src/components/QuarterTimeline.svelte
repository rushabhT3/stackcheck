<script lang="ts">
  import type { QuarterEvidence } from "../lib/types";

  let { quarters }: { quarters: QuarterEvidence[] } = $props();

  interface Point {
    label: string;
    claimed: number | null;
    low: number | null;
    up: number | null;
    open: boolean;
  }

  function claimedSlabs(q: QuarterEvidence): number | null {
    const block = q.filing.blocks[0];
    if (!block) return null;
    if (block.slabs_claimed_count !== null) return block.slabs_claimed_count;
    if (
      block.slabs_claimed_percent !== null &&
      block.floors_above_ground_sanctioned !== null
    ) {
      return Math.round(
        (block.slabs_claimed_percent / 100) * (block.floors_above_ground_sanctioned + 1),
      );
    }
    return null;
  }

  const points: Point[] = $derived(
    quarters.map((q) => {
    const usable = q.photos.filter(
      (p) => p.facts?.kind === "elevation" && p.floors?.status === "OK",
    );
    const lowers = usable.map((p) => p.floors?.lower).filter((v) => v !== null);
    const uppers = usable.map((p) => p.floors?.upper);
    return {
      label: q.filing.quarter_end ? q.filing.quarter_end.slice(0, 7) : "no date",
      claimed: claimedSlabs(q),
      low: lowers.length > 0 ? Math.min(...(lowers as number[])) : null,
      up:
        uppers.length > 0
          ? uppers.some((u) => u == null)
            ? null
            : Math.max(...(uppers as number[]))
          : null,
      open: uppers.length > 0 && uppers.some((u) => u == null),
    };
    }),
  );

  const maxV = $derived(
    Math.max(1, ...points.flatMap((p) => [p.claimed ?? 0, p.low ?? 0, p.up ?? 0])),
  );
  const yTicks = $derived([...new Set([0, Math.round(maxV / 2), maxV])]);
  const width = $derived(160 + points.length * 100);
  const axisY = 200;
  const y = (v: number): number => axisY - (v / maxV) * 160;
  const x = (i: number): number => 80 + i * 100;
</script>

<figure class="chart">
  <figcaption>
    <span class="eyebrow">Claim vs photo</span>
    <span class="chart-title display">Claimed slabs vs floors visible in photos</span>
  </figcaption>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex: scrollable chart region must be keyboard-focusable per WCAG 2.1.1 -->
  <div class="scroll-x" tabindex="0" role="region" aria-label="Timeline chart, scroll horizontally to explore">
    <svg
      viewBox={`0 0 ${width} 240`}
      role="img"
      aria-label="Claimed slab count against visible floor ranges per quarter"
    >
      <title>Claimed slabs and visible floor ranges per quarter</title>
      <line x1="40" y1={axisY} x2={width - 40} y2={axisY} class="axis" />
      {#each yTicks as t (t)}
        {#if t > 0}
          <line x1="40" y1={y(t)} x2={width - 40} y2={y(t)} class="grid" />
          <text x="34" y={y(t) + 4} text-anchor="end" class="axis-label">{t}</text>
        {/if}
      {/each}
      {#each points as p, i (i)}
        <text x={x(i)} y={axisY + 20} text-anchor="middle" class="axis-label">{p.label}</text>
        {#if p.low !== null}
          <rect
            x={x(i) - 11}
            y={p.up !== null ? y(p.up) : y(p.low) - 30}
            width="22"
            height={p.up !== null ? Math.max(4, y(p.low) - y(p.up)) : 30}
            rx="3"
            class="range"
          />
          {#if p.up === null}
            <text x={x(i)} y={y(p.low) - 36} text-anchor="middle" class="axis-label">
              ≥{p.low}
            </text>
          {/if}
        {/if}
        {#if p.claimed !== null}
          <circle cx={x(i)} cy={y(p.claimed)} r="7" class="claimed" />
          <text x={x(i) + 16} y={y(p.claimed) + 4} class="value-label">{p.claimed}</text>
        {:else}
          <rect x={x(i) - 5} y={axisY - 5} width="10" height="10" rx="2" class="nodata" />
          <text x={x(i)} y={axisY - 14} text-anchor="middle" class="axis-label">no data</text>
        {/if}
      {/each}
    </svg>
  </div>
  <div class="legend" aria-hidden="true">
    <span class="legend-key"><span class="dot claimed-dot"></span> claimed slabs</span>
    <span class="legend-key"><span class="swatch range-swatch"></span> visible floor range</span>
    <span class="legend-key"><span class="square nodata-square"></span> no data</span>
  </div>
  <p class="scroll-hint" aria-hidden="true">Swipe sideways to see the full chart</p>
</figure>

<style>
  .chart figcaption {
    display: grid;
    gap: 0.25rem;
  }

  .chart figcaption .eyebrow {
    margin-bottom: 0;
  }

  .chart-title {
    font-size: 1.5rem;
    line-height: 1.15;
  }

  .legend-key {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
  }
</style>
