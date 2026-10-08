<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from './Notice.svelte'
  import { ApiError } from '../lib/api'
  import { getWhy, resetUnderstanding, type Why } from '../lib/understanding'

  let { id, onclose, onchanged }: { id: string; onclose: () => void; onchanged: () => void } = $props()
  let why = $state<Why | null>(null)
  let error = $state('')
  let busy = $state(false)

  async function load() {
    try { why = await getWhy(id) } catch (err) { error = err instanceof ApiError ? err.detail : 'Could not load this.' }
  }
  onMount(load)

  async function decideAgain() {
    if (!why || busy) return
    error = ''
    busy = true
    try { await resetUnderstanding(id, why.version); onchanged() } catch (err) {
      const message = err instanceof ApiError ? err.detail : 'Something went wrong.'
      await load()
      error = message
    } finally { busy = false }
  }
</script>

<aside class="card why" aria-labelledby={`why-${id}`}>
  <h3 id={`why-${id}`}>Why is this {why?.category_path.at(-1) ?? 'here'}?</h3>
  <Notice message={error} />
  {#if why}
    <p><strong>{why.status_label}</strong>{#if why.decided_by_label} · decided by {why.decided_by_label}{/if}
      {#if why.decided_by && why.decided_by !== 'human'} · {Math.round(why.confidence * 100)}% sure{/if}</p>
    <ol>{#each why.steps as step}<li>{step}</li>{/each}</ol>
    {#if why.merchant}
      <p class="meta">Merchant: {why.merchant.name}{#if why.merchant.usual_category} · usually {why.merchant.usual_category}{/if} · seen {why.merchant.seen_count} times</p>
    {/if}
    <p class="meta">Decided with what Tuppence knew at version {why.knowledge_version} (now {why.current_knowledge_version}){why.stale ? ': it will look again' : ''}.</p>
    {#if why.history.length}
      <details>
        <summary>History</summary>
        <ul>{#each why.history as h}<li>{h.when}: {h.who} → {h.category}{h.reason ? ` (${h.reason})` : ''}</li>{/each}</ul>
      </details>
    {/if}
    {#if why.status !== 'unknown'}<button class="link" disabled={busy} onclick={decideAgain}>Let Tuppence decide again</button>{/if}
  {/if}
  <button onclick={onclose}>Close</button>
</aside>

<style>
  .why ol { padding-left: 1.25rem; }
</style>
