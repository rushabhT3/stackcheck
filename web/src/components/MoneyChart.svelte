<script lang="ts">
  import type { QuarterEvidence } from "../lib/types";

  let { quarters }: { quarters: QuarterEvidence[] } = $props();

  /** Mirror of the Python to_inr: lakh x 100,000, crore x 10,000,000. */
  function toInr(value: string | null, unit: string | null): number | null {
    if (value === null || value === "") return null;
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    const name = (unit ?? "INR").toUpperCase();
    if (name === "INR") return num;
    if (name === "INR_LAKH" || name === "LAKH") return num * 100_000;
    if (name === "INR_CRORE" || name === "CRORE") return num * 10_000_000;
    return null;
  }

  /** Mirror of the Python _progress_share: mean activity percent, else slabs over sanctioned. */
  function progressShare(q: QuarterEvidence): number | null {
    const values = q.filing.activities
      .map((a) => a.percent_done)
      .filter((v) => v !== null);
    if (values.length >= 3) {
      return values.reduce((a, b) => (a as number) + (b as number), 0) / values.length / 100;
    }
    const main = q.filing.blocks.find((b) => b.block_label === "MAIN");
    if (!main || main.floors_above_ground_sanctioned === null) return null;
    let slabs: number | null = main.slabs_claimed_count;
    if (slabs === null && main.slabs_claimed_percent !== null) {
      slabs = Math.round(
        (main.slabs_claimed_percent / 100) * (main.floors_above_ground_sanctioned + 1),
      );
    }
    if (slabs === null) return null;
    return slabs / (main.floors_above_ground_sanctioned + 1);
  }

  interface Series {
    label: string;
    spent: number | null;
    progress: number | null;
  }

  const series: Series[] = $derived(
    quarters.map((q) => {
    const fin = q.filing.financials;
    const spent = toInr(fin.cumulative_spent, fin.unit_overrides["cumulative_spent"] ?? fin.currency_unit);
    const total = toInr(
      fin.estimated_total_cost,
      fin.unit_overrides["estimated_total_cost"] ?? fin.currency_unit,
    );
    return {
      label: q.filing.quarter_end ? q.filing.quarter_end.slice(0, 7) : "no date",
      spent: spent !== null && total !== null && total > 0 ? (spent / total) * 100 : null,
      progress: (() => {
        const p = progressShare(q);
        return p === null ? null : p * 100;
      })(),
    };
    }),
  );

  const width = $derived(160 + series.length * 100);
  const axisY = 200;
  const y = (v: number): number => axisY - (Math.min(v, 100) / 100) * 160;
  const x = (i: number): number => 80 + i * 100;

  /** Contiguous runs of quarters with a value, so gaps break the line. */
  function runs(get: (s: Series) => number | null): number[][] {
    const out: number[][] = [];
    let cur: number[] = [];
    series.forEach((s, i) => {
      if (get(s) === null) {
        if (cur.length > 0) out.push(cur);
        cur = [];
        return;
      }
      cur.push(i);
    });
    if (cur.length > 0) out.push(cur);
    return out;
  }

  function linePath(idxs: number[], get: (s: Series) => number | null): string {
    return idxs
      .map((i, k) => `${k === 0 ? "M" : "L"}${x(i)},${y(get(series[i]) as number)}`)
      .join(" ");
  }

  function areaPath(idxs: number[], get: (s: Series) => number | null): string {
    const first = idxs[0];
    const last = idxs[idxs.length - 1];
    return `${linePath(idxs, get)} L${x(last)},${axisY} L${x(first)},${axisY} Z`;
  }

  function tri(i: number, v: number): string {
    return `${x(i)},${y(v) - 7} ${x(i) - 6},${y(v) + 5} ${x(i) + 6},${y(v) + 5}`;
  }

  /** Latest quarter where both series exist; drives the reference headline. */
  const gap: number | null = $derived(
    (() => {
      const last = [...series].reverse().find((s) => s.spent !== null && s.progress !== null);
      return last ? Math.round((last.spent as number) - (last.progress as number)) : null;
    })(),
  );
</script>

<figure class="chart">
  <figcaption>
    <span class="eyebrow">Follow the money</span>
    <span class="chart-title display">
      {gap !== null && gap > 15
        ? `Spending runs ${gap} points ahead of work`
        : "Money spent vs work progress, per quarter"}
    </span>
  </figcaption>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex: scrollable chart region must be keyboard-focusable per WCAG 2.1.1 -->
  <div class="scroll-x" tabindex="0" role="region" aria-label="Money chart, scroll horizontally to explore">
    <svg
      viewBox={`0 0 ${width} 240`}
      role="img"
      aria-label="Share of money spent against share of work done per quarter"
    >
      <title>Money spent versus work progress per quarter</title>
      <line x1="40" y1={axisY} x2={width - 40} y2={axisY} class="axis" />
      {#each [0, 25, 50, 75, 100] as tick (tick)}
        <line x1="40" y1={y(tick)} x2={width - 40} y2={y(tick)} class="grid" />
        <text x="34" y={y(tick) + 4} text-anchor="end" class="axis-label">{tick}%</text>
      {/each}
      {#each runs((s) => s.progress) as run (run[0])}
        <path d={areaPath(run, (s) => s.progress)} class="area-progress" />
        <path d={linePath(run, (s) => s.progress)} class="line progress" />
      {/each}
      {#each runs((s) => s.spent) as run (run[0])}
        <path d={linePath(run, (s) => s.spent)} class="line spent" />
      {/each}
      {#each series as s, i (i)}
        <text x={x(i)} y={axisY + 20} text-anchor="middle" class="axis-label">{s.label}</text>
        {#if s.spent !== null}
          <polygon points={tri(i, s.spent)} class="spent-dot" />
        {/if}
        {#if s.progress !== null}
          <circle cx={x(i)} cy={y(s.progress)} r="5" class="progress-dot" />
        {/if}
        {#if s.spent === null && s.progress === null}
          <rect x={x(i) - 5} y={axisY - 5} width="10" height="10" rx="2" class="nodata" />
          <text x={x(i)} y={axisY - 14} text-anchor="middle" class="axis-label">no data</text>
        {/if}
      {/each}
      {#each runs((s) => s.spent) as run (run[run.length - 1])}
        {@const v = series[run[run.length - 1]].spent as number}
        <text x={x(run[run.length - 1]) + 11} y={y(v) + 4} class="value-label">
          {Math.round(v)}%
        </text>
      {/each}
      {#each runs((s) => s.progress) as run (run[run.length - 1])}
        {@const v = series[run[run.length - 1]].progress as number}
        <text x={x(run[run.length - 1]) + 11} y={y(v) + 4} class="value-label">
          {Math.round(v)}%
        </text>
      {/each}
    </svg>
  </div>
  <div class="legend" aria-hidden="true">
    <span class="legend-key"><span class="line-key spent-key"></span> money spent</span>
    <span class="legend-key"><span class="line-key progress-key"></span> work progress</span>
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
