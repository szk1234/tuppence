<script lang="ts">
  import { onMount } from 'svelte'
  import DebtForm from '../../components/forms/DebtForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Debt, Person } from '../../lib/types'

  let people = $state<Person[]>([])
  let debts = $state<Debt[]>([])
  let loaded = $state(false)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)
  const active = $derived(debts.filter((d) => d.status === 'active'))

  onMount(async () => {
    try {
      people = (await api<{ people: Person[] }>('/api/household/people')).people
      debts = (await api<{ debts: Debt[] }>('/api/debts')).debts
      loaded = true
    } catch (err) { error = errorText(err) }
  })

  async function add(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const d = await api<Debt>('/api/debts', { method: 'POST', body })
      debts = [...debts, d]; saved = `${d.lender} added.`; addKey += 1
      return true
    } catch (err) { error = errorText(err); return false }
  }
</script>

<p>Loans, car finance, a mortgage, a student loan or an overdraft. Skip this if you have none.</p>
<Notice message={error} />
<Notice message={saved} kind="ok" />

<div class="card">
  <h2>Your debts</h2>
  {#if !loaded}<p class="hint">Loading…</p>{:else if active.length === 0}<p>No debts added.</p>{/if}
  <ul class="items">
    {#each active as d (d.id)}
      <li>
        <span class="name">{d.lender}</span>
        <span class="meta">{formatGBP(d.balance)} outstanding{d.apr !== null ? ` · ${d.apr}% APR` : ''}</span>
        {#if d.car_finance_redress_window}<span class="meta">This agreement may fall in the car finance redress window.</span>{/if}
      </li>
    {/each}
  </ul>
</div>

<div class="card">
  <h2>Add a debt</h2>
  {#if loaded}{#key addKey}<DebtForm {people} onsubmit={add} />{/key}{:else}<p class="hint">Loading…</p>{/if}
</div>
