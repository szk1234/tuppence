<script lang="ts">
  import { onMount } from 'svelte'
  import IncomeForm from '../../components/forms/IncomeForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { ukDate } from '../../lib/dates'
  import { errorText, isConflict } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import { link } from '../../lib/router.svelte'
  import { needsAccount, type Account, type Income, type Person } from '../../lib/types'

  let people = $state<Person[]>([])
  let accounts = $state<Account[]>([])
  let incomes = $state<Income[]>([])
  let loaded = $state(false)
  let editing = $state<string | null>(null)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)

  const active = $derived(incomes.filter((i) => i.status === 'active'))
  const ended = $derived(incomes.filter((i) => i.status === 'ended'))
  const assumed = $derived(active.some((i) => i.calendar_assumed))
  const who = (i: Income) => people.find((p) => p.id === i.person_id)?.display_name ?? 'Someone who has left'
  const into = (i: Income) => accounts.find((a) => a.id === i.account_id)?.nickname
  /** Where the pay lands; an account that closed or isn't theirs any more counts as not chosen. */
  const landing = (i: Income) => {
    if (!i.account_id) return ' · account not chosen yet'
    if (needsAccount(i) || !into(i)) return ' · its account has closed or is no longer theirs: choose another'
    return ` · paid into ${into(i)}`
  }

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    accounts = (await api<{ accounts: Account[] }>('/api/accounts')).accounts
    incomes = (await api<{ income: Income[] }>('/api/income?include_ended=true')).income
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
      const i = await api<Income>('/api/income', { method: 'POST', body })
      incomes = [...incomes, i]; saved = `${i.name} added.`; addKey += 1
      return true
    } catch (err) { fail(err); return false }
  }

  async function save(i: Income, changes: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const updated = await api<Income>(`/api/income/${i.id}`, { method: 'PATCH', body: { changes, expected_version: i.version } })
      incomes = incomes.map((x) => (x.id === i.id ? updated : x)); editing = null; saved = `${updated.name} saved.`
      return true
    } catch (err) { fail(err); return false }
  }

  async function end(i: Income) {
    error = ''; saved = ''
    try {
      const updated = await api<Income>(`/api/income/${i.id}/end`, { method: 'POST', body: { expected_version: i.version } })
      incomes = incomes.map((x) => (x.id === i.id ? updated : x)); saved = `${i.name} ended.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Income</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  {#if assumed}
    <p class="hint"><span>Set your nation for accurate bank holidays.</span> <a href="/settings/household" onclick={link}>Open Household</a></p>
  {/if}

  <div class="card">
    <h2>Your income</h2>
    {#if !loaded}<p class="hint">Loading…</p>
    {:else if active.length === 0}<p>No income added yet.</p>{/if}
    <ul class="items">
      {#each active as i (i.id)}
        <li>
          {#if editing === i.id}
            <IncomeForm {people} {accounts} initial={i} onsubmit={(c) => save(i, c)} oncancel={() => (editing = null)} />
          {:else}
            <span class="name">{i.name}</span>
            <span class="meta">
              {who(i)} · {formatGBP(i.net_amount)} · {i.pay_rule_description}{i.next_pay_date ? ` · next payday ${ukDate(i.next_pay_date)}` : ''}{landing(i)}
              {#if i.person_left}<span class="badge cloud">Has left the household</span>{/if}
            </span>
            <span class="actions">
              <button class="link" onclick={() => (editing = i.id)} aria-label={`Edit ${i.name}`}>Edit</button>
              <button class="link" onclick={() => end(i)} aria-label={`End ${i.name}`}>End</button>
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if ended.length}
    <div class="card">
      <h2>Ended</h2>
      <ul class="items">
        {#each ended as i (i.id)}
          <li><span class="name">{i.name}</span><span class="meta">{who(i)} · {formatGBP(i.net_amount)} · {i.pay_rule_description}</span></li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if editing === null}
    <div class="card">
      <h2>Add income</h2>
      {#if loaded}{#key addKey}<IncomeForm {people} {accounts} onsubmit={add} />{/key}{:else}<p class="hint">Loading…</p>{/if}
    </div>
  {/if}
</section>
