<script lang="ts">
  import { onMount } from 'svelte'
  import GoalForm from '../../components/forms/GoalForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { ukDate } from '../../lib/dates'
  import { errorText, isConflict } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Goal } from '../../lib/types'

  const PRIORITIES = { 1: 'High priority', 2: 'Medium priority', 3: 'Low priority' }
  let goals = $state<Goal[]>([])
  let suggest = $state(false)
  let loaded = $state(false)
  let editing = $state<string | null>(null)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)

  const active = $derived(goals.filter((g) => g.status === 'active'))
  const closed = $derived(goals.filter((g) => g.status !== 'active'))

  async function load() {
    goals = (await api<{ goals: Goal[] }>('/api/goals?include_closed=true')).goals
    // The suggestion is a nicety: the page works without it.
    suggest = await api<{ emergency_fund?: boolean }>('/api/goals/suggestions').then((s) => s?.emergency_fund === true).catch(() => false)
    loaded = true
  }
  onMount(() => { load().catch(fail) })

  function fail(err: unknown) {
    saved = ''; error = errorText(err)
    if (isConflict(err)) load().catch(() => {})
  }

  async function add(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const g = await api<Goal>('/api/goals', { method: 'POST', body })
      goals = [...goals, g]; saved = `${g.name} added.`; addKey += 1
      if (g.kind === 'emergency_fund') suggest = false
      return true
    } catch (err) { fail(err); return false }
  }

  /** The suggested emergency fund: added without resetting the form, so a goal being typed stays. */
  async function addSuggested(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const g = await api<Goal>('/api/goals', { method: 'POST', body })
      goals = [...goals, g]; saved = `${g.name} added.`; suggest = false
      return true
    } catch (err) { fail(err); return false }
  }

  async function save(g: Goal, changes: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const updated = await api<Goal>(`/api/goals/${g.id}`, { method: 'PATCH', body: { changes, expected_version: g.version } })
      goals = goals.map((x) => (x.id === g.id ? updated : x)); editing = null; saved = `${updated.name} saved.`
      return true
    } catch (err) { fail(err); return false }
  }

  async function setStatus(g: Goal, status: Goal['status'], message: string) {
    error = ''; saved = ''
    try {
      const updated = await api<Goal>(`/api/goals/${g.id}/status`, { method: 'POST', body: { status, expected_version: g.version } })
      goals = goals.map((x) => (x.id === g.id ? updated : x)); saved = message
    } catch (err) { fail(err) }
  }

  const progress = (g: Goal) => `${formatGBP(g.saved_amount)}${g.target_amount ? ` of ${formatGBP(g.target_amount)}` : ' saved'}`
</script>

<section>
  <h1>Goals</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <div class="card">
    <h2>Your goals</h2>
    {#if !loaded}<p class="hint">Loading…</p>
    {:else if active.length === 0}<p>No goals yet.</p>{/if}
    <ul class="items">
      {#each active as g (g.id)}
        <li>
          {#if editing === g.id}
            <GoalForm initial={g} onsubmit={(c) => save(g, c)} oncancel={() => (editing = null)} />
          {:else}
            <span class="name">{g.name}</span>
            <span class="meta">{progress(g)} · {PRIORITIES[g.priority]}{g.target_date ? ` · by ${ukDate(g.target_date)}` : ''}</span>
            <span class="actions">
              <button class="link" onclick={() => (editing = g.id)} aria-label={`Edit ${g.name}`}>Edit</button>
              <button class="link" onclick={() => setStatus(g, 'achieved', `${g.name} marked as achieved.`)} aria-label={`Mark ${g.name} achieved`}>Achieved</button>
              <button class="link" onclick={() => setStatus(g, 'abandoned', `${g.name} dropped.`)} aria-label={`Drop ${g.name}`}>Drop</button>
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if closed.length}
    <div class="card">
      <h2>Achieved or dropped</h2>
      <ul class="items">
        {#each closed as g (g.id)}
          <li>
            <span class="name">{g.name}</span><span class="meta">{g.status === 'achieved' ? 'Achieved' : 'Dropped'} · {progress(g)}</span>
            <span class="actions"><button class="link" onclick={() => setStatus(g, 'active', `${g.name} is active again.`)} aria-label={`Reopen ${g.name}`}>Reopen</button></span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if editing === null}
    <div class="card">
      <h2>Add a goal</h2>
      {#if loaded}{#key addKey}<GoalForm suggestEmergencyFund={suggest} onsubmit={add} onaddsuggested={addSuggested} />{/key}{:else}<p class="hint">Loading…</p>{/if}
    </div>
  {/if}
</section>
