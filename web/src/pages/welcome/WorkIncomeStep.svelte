<script lang="ts">
  import { onMount } from 'svelte'
  import IncomeForm from '../../components/forms/IncomeForm.svelte'
  import PersonAttributes from '../../components/forms/PersonAttributes.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Account, Income, Person } from '../../lib/types'

  let people = $state<Person[]>([])
  let accounts = $state<Account[]>([])
  let incomes = $state<Income[]>([])
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
  // Anyone who isn't a child may record work and income (optional for a dependent adult: nothing prompts for it).
  const earners = $derived(people.filter((p) => p.role !== 'child'))
  const active = $derived(incomes.filter((i) => i.status === 'active'))
  const who = (i: Income) => people.find((p) => p.id === i.person_id)?.display_name ?? 'Someone who has left'

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    accounts = (await api<{ accounts: Account[] }>('/api/accounts')).accounts
    incomes = (await api<{ income: Income[] }>('/api/income')).income
    loaded = true
  }
  onMount(() => { load().catch((err) => { error = errorText(err) }) })

  const attributeSaved = (message: string) => { error = ''; saved = message }
  const attributeFailed = (message: string) => { saved = ''; error = message }

  async function addIncome(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const i = await api<Income>('/api/income', { method: 'POST', body })
      incomes = [...incomes, i]; saved = `${i.name} added.`; addKey += 1; dirty = false
      return true
    } catch (err) { error = errorText(err); return false }
  }
</script>

<Notice message={error} />
<Notice message={saved} kind="ok" />

{#each earners as p (p.id)}
  <section class="card" aria-label={p.display_name}>
    <h2>{p.display_name}</h2>
    <PersonAttributes person={p} onsaved={attributeSaved} onerror={attributeFailed} />
  </section>
{/each}
{#if loaded && earners.length === 0}<p>Add an adult in the previous step to record work and income.</p>{/if}

<div class="card">
  <h2>Your income</h2>
  {#if active.length === 0}<p>No income added yet. Add what lands in your account each time you're paid.</p>{/if}
  <ul class="items">
    {#each active as i (i.id)}
      <li><span class="name">{i.name}</span><span class="meta">{who(i)} · {formatGBP(i.net_amount)} · {i.pay_rule_description}</span></li>
    {/each}
  </ul>
  {#if loaded && earners.length > 0}
    <h3>Add income</h3>
    <div oninput={() => (dirty = true)} onchange={() => (dirty = true)}>{#key addKey}<IncomeForm bind:this={form} {people} {accounts} onsubmit={addIncome} />{/key}</div>
    <p class="hint">Not added your accounts yet? Leave "Paid into" as it is: you'll be asked in the Accounts step.</p>
  {/if}
</div>
