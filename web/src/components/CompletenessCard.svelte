<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../lib/api'
  import { link } from '../lib/router.svelte'

  type Prompt = { id: string; text: string; unlocks: string; link: string }
  let completeness = $state<number | null>(null)
  let prompts = $state<Prompt[]>([])
  let finished = $state(false)

  const validPrompt = (p: unknown): p is Prompt =>
    typeof p === 'object' && p !== null && typeof (p as Prompt).text === 'string' && typeof (p as Prompt).link === 'string'

  onMount(async () => {
    try {
      const res = await api<Record<string, unknown>>('/api/onboarding')
      if (typeof res?.completeness !== 'number') return
      prompts = Array.isArray(res.prompts) ? res.prompts.filter(validPrompt).slice(0, 3) : []
      finished = res.finished === true
      completeness = Math.max(0, Math.min(100, Math.round(res.completeness)))
    } catch { /* The card is a nicety; Home still works without it. */ }
  })
</script>

{#if completeness !== null && (completeness < 100 || prompts.length > 0)}
  <section class="card" aria-labelledby="completeness-title">
    <h2 id="completeness-title">Your profile is {completeness}% complete</h2>
    <progress max="100" value={completeness} aria-label="Profile completeness">{completeness}%</progress>
    {#if prompts.length}
      <ul class="items">
        {#each prompts as p (p.id)}
          <li><a href={p.link} onclick={link}>{p.text}</a>{#if p.unlocks}<span class="meta">Unlocks: {p.unlocks}</span>{/if}</li>
        {/each}
      </ul>
    {/if}
    {#if !finished}<p><a href="/welcome" onclick={link}>Continue setup</a></p>{/if}
  </section>
{/if}
