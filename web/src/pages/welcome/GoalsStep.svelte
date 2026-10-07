<script lang="ts">
  import { onMount } from 'svelte'
  import GoalForm from '../../components/forms/GoalForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { ukDate } from '../../lib/dates'
  import { errorText } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Goal } from '../../lib/types'

  let goals = $state<Goal[]>([])
  let suggest = $state(false)
  let loaded = $state(false)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)
  /** The form below holds typed-but-unsaved input; Continue saves it first (an untouched form just continues). */
  let form = $state<{ save: () => Promise<boolean> }>()
  let dirty = false
  export async function save(): Promise<boolean> {
    if (!dirty || !form) return true
    return form.save()
  }
  const active = $derived(goals.filter((g) => g.status === 'active'))

  async function load() {
    goals = (await api<{ goals: Goal[] }>('/api/goals')).goals
    suggest = (await api<{ emergency_fund: boolean }>('/api/goals/suggestions')).emergency_fund === true
    loaded = true
  }
  onMount(() => { load().catch((err) => { error = errorText(err) }) })

  async function add(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const g = await api<Goal>('/api/goals', { method: 'POST', body })
      goals = [...goals, g]; saved = `${g.name} added.`; addKey += 1; dirty = false
    } catch (err) { error = errorText(err); return false }
    suggest = await api<{ emergency_fund: boolean }>('/api/goals/suggestions').then((r) => r.emergency_fund === true).catch(() => suggest)
    return true
  }
</script>

<p>What are you saving or aiming for? Goals help Tuppence suggest where spare money could go.</p>
<Notice message={error} />
<Notice message={saved} kind="ok" />

<div class="card">
  <h2>Your goals</h2>
  {#if !loaded}<p class="hint">Loading…</p>{:else if active.length === 0}<p>No goals yet.</p>{/if}
  <ul class="items">
    {#each active as g (g.id)}
      <li><span class="name">{g.name}</span><span class="meta">{g.target_amount ? `target ${formatGBP(g.target_amount)}` : 'no target set'}{g.target_date ? ` by ${ukDate(g.target_date)}` : ''}</span></li>
    {/each}
  </ul>
</div>

<div class="card">
  <h2>Add a goal</h2>
  {#if loaded}<div oninput={() => (dirty = true)} onchange={() => (dirty = true)}>{#key addKey}<GoalForm bind:this={form} suggestEmergencyFund={suggest} onsubmit={add} />{/key}</div>{:else}<p class="hint">Loading…</p>{/if}
</div>
