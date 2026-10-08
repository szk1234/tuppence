<script lang="ts">
  import { onMount } from 'svelte'
  import { formatGBP } from '../lib/money'
  import { link } from '../lib/router.svelte'
  import { dmyDate } from '../lib/dates'
  import { getHomeSummary, type HomeSummary } from '../lib/understanding'

  let summary = $state<HomeSummary | null>(null)
  onMount(async () => {
    try {
      const s = await getHomeSummary()
      // Only a summary of the expected shape is shown (an older server, or a stub, says something else).
      summary = Array.isArray(s?.top) && Array.isArray(s?.due_soon) && s?.analysis?.waiting ? s : null
    } catch { summary = null }
  })
</script>

{#if summary}
  <div class="row cards">
    <section class="card" aria-labelledby="home-spending">
      <h2 id="home-spending">Spending · {summary.period.label}</h2>
      <p class="big">{formatGBP(summary.spent)}</p>
      <ul>{#each summary.top as t (t.id)}<li>{t.label}: {formatGBP(t.amount)}</li>{/each}</ul>
      <a href="/spending" onclick={link}>See where it went</a>
    </section>
    <section class="card" aria-labelledby="home-due">
      <h2 id="home-due">Due in the next 7 days</h2>
      {#if summary.due_soon.length}
        <ul>{#each summary.due_soon as d (d.commitment_id + d.date)}<li>{dmyDate(d.date)}: {d.name} {formatGBP(d.amount)}</li>{/each}</ul>
      {:else}<p>Nothing due.</p>{/if}
      <a href="/commitments" onclick={link}>All commitments</a>
    </section>
  </div>
  {#if summary.analysis.waiting.awaiting_ai}
    <p class="notice error" role="alert">{summary.analysis.waiting.awaiting_ai} transactions are waiting for an AI model. Choose one in <a href="/settings/ai" onclick={link}>Settings › AI</a>.</p>
  {/if}
  {#if summary.analysis.running || summary.analysis.queued}
    <p role="status">Sorting your transactions…</p>
  {:else if summary.analysis.last_run}
    <p class="meta">Last look: {summary.analysis.last_run.summary}</p>
  {/if}
{/if}

<style>
  .cards .card { flex: 1; min-width: 15rem; }
  .cards h2 { font-size: 1rem; margin: 0 0 .25rem; }
  .big { font-size: 1.6rem; font-weight: 700; margin: 0; font-variant-numeric: tabular-nums; }
</style>
