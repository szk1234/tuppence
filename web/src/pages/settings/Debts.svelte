<script lang="ts">
  import { onMount } from 'svelte'
  import DebtForm from '../../components/forms/DebtForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText, isConflict } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Debt, Person } from '../../lib/types'

  const KINDS: Record<string, string> = {
    personal_loan: 'Personal loan', car_finance_pcp: 'Car finance (PCP)', car_finance_hp: 'Car finance (HP)', mortgage: 'Mortgage',
    student_loan: 'Student loan', bnpl: 'Buy now, pay later', overdraft: 'Overdraft', informal: 'Money owed to or by someone you know', other: 'Other',
  }
  let people = $state<Person[]>([])
  let debts = $state<Debt[]>([])
  let loaded = $state(false)
  let editing = $state<string | null>(null)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)

  const open = $derived(debts.filter((d) => d.status === 'active'))
  const settled = $derived(debts.filter((d) => d.status === 'settled'))
  const owner = (d: Debt) => people.find((p) => p.id === d.person_id)?.display_name

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    debts = (await api<{ debts: Debt[] }>('/api/debts?include_settled=true')).debts
    loaded = true
  }
  onMount(() => { load().catch(fail) })

  function fail(err: unknown) {
    saved = ''; error = errorText(err)
    if (isConflict(err)) load().catch(() => {})
  }

  function removedNote(d: Debt) {
    return d.details_removed?.length ? ` Details that no longer apply were removed: ${d.details_removed.join(', ')}.` : ''
  }

  async function add(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const d = await api<Debt>('/api/debts', { method: 'POST', body })
      debts = [...debts, d]; saved = `${d.lender} added.`; addKey += 1
      return true
    } catch (err) { fail(err); return false }
  }

  async function save(d: Debt, changes: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const updated = await api<Debt>(`/api/debts/${d.id}`, { method: 'PATCH', body: { changes, expected_version: d.version } })
      debts = debts.map((x) => (x.id === d.id ? updated : x)); editing = null; saved = `${updated.lender} saved.${removedNote(updated)}`
      return true
    } catch (err) { fail(err); return false }
  }

  async function setStatus(d: Debt, action: 'settle' | 'reopen') {
    error = ''; saved = ''
    try {
      const updated = await api<Debt>(`/api/debts/${d.id}/${action}`, { method: 'POST', body: { expected_version: d.version } })
      debts = debts.map((x) => (x.id === d.id ? updated : x))
      saved = action === 'settle' ? `${d.lender} marked as settled.` : `${d.lender} reopened.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Debts</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <div class="card">
    <h2>Your debts</h2>
    {#if !loaded}<p class="hint">Loading…</p>
    {:else if open.length === 0}<p>No debts added. That's fine if you don't have any.</p>{/if}
    <ul class="items">
      {#each open as d (d.id)}
        <li>
          {#if editing === d.id}
            <DebtForm {people} initial={d} onsubmit={(c) => save(d, c)} oncancel={() => (editing = null)} />
          {:else}
            <span class="name">{d.lender}</span>
            <span class="meta">
              {KINDS[d.kind] ?? d.kind} · {formatGBP(d.balance)}{d.apr != null ? ` · ${d.apr}% APR` : ''}{d.monthly_payment ? ` · ${formatGBP(d.monthly_payment)} a month` : ''}{owner(d) ? ` · ${owner(d)}` : ''}
              {#if d.car_finance_redress_window}<span class="badge">May be covered by the FCA motor finance review</span>{/if}
            </span>
            <span class="actions">
              <button class="link" onclick={() => (editing = d.id)} aria-label={`Edit ${d.lender}`}>Edit</button>
              <button class="link" onclick={() => setStatus(d, 'settle')} aria-label={`Settle ${d.lender}`}>Mark settled</button>
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if settled.length}
    <div class="card">
      <h2>Settled</h2>
      <ul class="items">
        {#each settled as d (d.id)}
          <li>
            <span class="name">{d.lender}</span><span class="meta">{KINDS[d.kind] ?? d.kind}</span>
            <span class="actions"><button class="link" onclick={() => setStatus(d, 'reopen')} aria-label={`Reopen ${d.lender}`}>Reopen</button></span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if editing === null}
    <div class="card">
      <h2>Add a debt</h2>
      {#if loaded}{#key addKey}<DebtForm {people} onsubmit={add} />{/key}{:else}<p class="hint">Loading…</p>{/if}
    </div>
  {/if}
</section>
