<script lang="ts">
  import StartView from "./views/StartView.svelte";
  import ProgressView from "./views/ProgressView.svelte";
  import ReportView from "./views/ReportView.svelte";
  import AccuracyView from "./views/AccuracyView.svelte";
  import { navigate, router } from "./lib/router.svelte";
  import { getSamples } from "./lib/api";

  const route = $derived(router.route);
  const dark = $derived(route.name === "start");

  let hasSamples = $state(true);

  $effect(() => {
    let alive = true;
    getSamples()
      .then((s) => {
        if (alive) hasSamples = s.length > 0;
      })
      .catch(() => {
        if (alive) hasSamples = false;
      });
    return () => {
      alive = false;
    };
  });

  $effect(() => {
    if (route.name !== "start") window.scrollTo({ top: 0 });
  });

  function scrollToSection(id: string): void {
    const go = (): void => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    const h = window.location.hash;
    if (h !== "" && h !== "#/") {
      navigate("#/");
      window.setTimeout(go, 120);
    } else {
      go();
    }
  }

  function skipToMain(e: MouseEvent): void {
    e.preventDefault();
    document.getElementById("main")?.focus();
  }
</script>

<a class="skip-link" href="#main" onclick={skipToMain}>Skip to content</a>
<header class="site-header" class:light={!dark}>
  <div class="wrap">
    <p class="brand">
      <a href="#/" aria-label="StackCheck home">Stack<em>Check</em></a>
    </p>
    <nav class="site-nav" aria-label="Sections">
      <button type="button" onclick={() => scrollToSection("how")}>How it works</button>
      {#if hasSamples}
        <button type="button" onclick={() => scrollToSection("samples")}>Sample reports</button>
      {/if}
      <a href="#/accuracy">How accurate is this?</a>
    </nav>
  </div>
</header>
<main id="main" tabindex="-1">
  <div class="wrap">
    {#if route.name === "check"}
      {#key route.id}
        <ProgressView id={route.id} />
      {/key}
    {:else if route.name === "report"}
      {#key route.id}
        <ReportView id={route.id} />
      {/key}
    {:else if route.name === "accuracy"}
      <AccuracyView />
    {:else}
      <StartView />
    {/if}
  </div>
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>StackCheck · Meta Global AI Developer Hackathon</p>
    <p class="statement">A gap is a <em>question</em>,<br />not proof of wrongdoing.</p>
    <p><a href="#/accuracy">How accurate is this?</a></p>
  </div>
</footer>

<style>
  .site-nav button {
    display: inline-flex;
    align-items: center;
    min-height: 2.75rem;
    padding: 0.2rem 0.25rem;
    border: 0;
    background: transparent;
    color: rgb(255 255 255 / 0.75);
    font: inherit;
    font-size: 0.875rem;
    font-weight: 600;
    cursor: pointer;
    transition: color 160ms ease;
  }

  .site-nav button:hover {
    color: var(--color-lime);
  }

  .site-header.light {
    background: var(--color-paper);
    color: var(--color-ink);
    border-bottom: 2px solid var(--color-ink);
  }

  .site-header.light .brand a {
    color: var(--color-ink);
  }

  .site-header.light .brand a::before {
    border-color: var(--color-ink);
  }

  .site-header.light .site-nav button {
    color: rgb(11 21 16 / 0.7);
  }

  .site-header.light .site-nav button:hover {
    color: var(--color-ink);
  }

  .site-header.light .site-nav a:last-child {
    border-color: var(--color-ink);
    color: var(--color-ink);
  }

  .site-header.light .site-nav a:last-child:hover {
    background: var(--color-ink);
    color: var(--color-paper);
  }

  .brand em {
    font-style: italic;
    font-variation-settings: "wght" 400;
  }

  .statement em {
    color: var(--color-lime);
  }

  main:focus {
    outline: none;
  }
</style>
