<script lang="ts">
  import type { AgentStep } from "../lib/types";

  let { trace }: { trace: AgentStep[] } = $props();
</script>

<details class="trace">
  <summary>Investigator trace ({trace.length} steps)</summary>
  {#if trace.length === 0}
    <p>No investigator steps were needed.</p>
  {:else}
    <ol>
      {#each trace as step (step.step)}
        <li>
          <span class="step-num" aria-hidden="true">{String(step.step).padStart(2, "0")}</span>
          <div class="step-body">
            <p class="step-action">
              <span class="action-pill">{step.action}</span>{#if step.argument}<span
                  class="step-arg"
                >
                  {step.argument}</span
                >{/if}
            </p>
            <p class="step-result">→ {step.result}</p>
            <p class="step-meta">On {step.finding_id}: {step.rationale}</p>
          </div>
        </li>
      {/each}
    </ol>
  {/if}
</details>

<style>
  .trace li {
    display: grid;
    grid-template-columns: 3rem 1fr;
    gap: 0.75rem;
  }

  .step-num {
    font-family: var(--font-display);
    font-size: 1.875rem;
    line-height: 1;
    color: var(--color-lime);
  }

  .step-body p {
    margin: 0;
  }

  .step-action {
    font-family: var(--font-mono);
    font-size: 0.875rem;
  }

  .action-pill {
    display: inline-block;
    background: var(--color-lime);
    color: var(--color-ink);
    font-weight: 700;
    border-radius: 4px;
    padding: 0.125rem 0.375rem;
  }

  .step-arg {
    margin-left: 0.5rem;
    color: var(--color-lime);
  }

  .step-result {
    margin-top: 0.5rem;
    color: rgb(255 255 255 / 0.9);
  }

  .step-meta {
    margin-top: 0.25rem;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: rgb(255 255 255 / 0.5);
  }
</style>
