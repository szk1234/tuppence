<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { ApiError } from '../../lib/api'
  import { disableRule, listRules, type RuleView } from '../../lib/understanding'

  let rules = $state<RuleView[]>([])
  let error = $state('')
  let saved = $state('')
  let busy = $state(false)
  let ready = $state(false)
  const locked = $derived(busy || !ready)

  async function load() {
    try { rules = await listRules() } catch (err) { error = err instanceof ApiError ? err.detail : 'Could not load your rules.' }
  }
  onMount(async () => { await load(); ready = true })

  async function switchOff(rule: RuleView) {
    if (locked) return
    error = ''
    saved = ''
    busy = true
    try {
      const out = await disableRule(rule.id, rule.version)
      saved = `Switched off. ${out.released} transaction${out.released === 1 ? '' : 's'} will be looked at again.`
      await load()
    } catch (err) {
      error = err instanceof ApiError ? err.detail : 'Something went wrong.'
      saved = ''
      await load()
    } finally { busy = false }
  }

  const mine = $derived(rules.filter((r) => r.source !== 'seed'))
  const builtIn = $derived(rules.filter((r) => r.source === 'seed'))
</script>

<section>
  <h1>Rules</h1>
  <p>Rules file payments the same way every time, with no AI. Make one from the Spending page when you change a category.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  <h2>Your rules</h2>
  {#if mine.length === 0}<p>No rules yet.</p>{/if}
  <ul class="rules">
    {#each mine as rule (rule.id)}
      <li>{rule.description} <span class="meta">· used {rule.hit_count} times</span>
        <button class="link" disabled={locked} onclick={() => switchOff(rule)} aria-label={`Switch off: ${rule.description}`}>Switch off</button></li>
    {/each}
  </ul>
  <h2>Built in</h2>
  <ul class="rules">
    {#each builtIn as rule (rule.id)}
      <li>{rule.description} <button class="link" disabled={locked} onclick={() => switchOff(rule)} aria-label={`Switch off: ${rule.description}`}>Switch off</button></li>
    {/each}
  </ul>
</section>

<style>
  .rules { list-style: none; padding: 0; }
  .rules li { padding: .4rem 0; border-bottom: 1px solid var(--line); }
</style>
