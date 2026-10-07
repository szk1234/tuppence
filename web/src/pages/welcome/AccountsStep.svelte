<script lang="ts">
  import { onMount } from 'svelte'
  import AccountForm from '../../components/forms/AccountForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText, isConflict } from '../../lib/form'
  import { needsAccount, type Account, type Income, type Person } from '../../lib/types'

  const KINDS = { current: 'Current account', savings: 'Savings account', credit_card: 'Credit card' }
  let people = $state<Person[]>([])
  let accounts = $state<Account[]>([])
  let incomes = $state<Income[]>([])
  let loaded = $state(false)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)
  let linking = $state(false)

  /** The form below holds typed-but-unsaved input; Continue saves it first (an untouched form just continues). */
  let form = $state<{ save: () => Promise<boolean> }>()
  let dirty = false
  export async function save(): Promise<boolean> {
    if (!dirty || !form) return true
    return form.save()
  }
  const active = $derived(accounts.filter((a) => a.status === 'active'))
  // No receiving account yet, or one that has closed or isn't the earner's any more: ask (again).
  const unplaced = $derived(incomes.filter(needsAccount))
  const options = (i: Income) => active.filter((a) => a.owner_ids.includes(i.person_id))
  const owners = (a: Account) => a.owner_ids.map((id) => people.find((p) => p.id === id)?.display_name ?? 'Someone who has left').join(', ')

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    accounts = (await api<{ accounts: Account[] }>('/api/accounts')).accounts
    incomes = (await api<{ income: Income[] }>('/api/income')).income
    loaded = true
  }
  onMount(() => { load().catch((err) => { error = errorText(err) }) })

  async function add(body: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const a = await api<Account>('/api/accounts', { method: 'POST', body })
      accounts = [...accounts, a]; saved = `${a.nickname} added.`; addKey += 1; dirty = false
      return true
    } catch (err) { error = errorText(err); return false }
  }

  async function paidInto(i: Income, accountId: string) {
    if (!accountId) return
    error = ''; saved = ''; linking = true
    try {
      const updated = await api<Income>(`/api/income/${i.id}`, { method: 'PATCH', body: { changes: { account_id: accountId }, expected_version: i.version } })
      incomes = incomes.map((x) => (x.id === i.id ? updated : x))
      saved = `${i.name} will be paid into ${accounts.find((a) => a.id === accountId)?.nickname}.`
    } catch (err) {
      error = errorText(err)
      if (isConflict(err)) await load().catch(() => {})
    } finally { linking = false }
  }
</script>

<p>Add your current accounts, savings and credit cards. We only ever ask for the last 4 digits, never full account numbers or sort codes.</p>
<Notice message={error} />
<Notice message={saved} kind="ok" />

<div class="card">
  <h2>Your accounts</h2>
  {#if !loaded}<p class="hint">Loading…</p>{:else if active.length === 0}<p>No accounts yet.</p>{/if}
  <ul class="items">
    {#each active as a (a.id)}
      <li><span class="name">{a.nickname}</span><span class="meta">{a.provider_name} · {KINDS[a.kind]}{a.last4 ? ` · ending ${a.last4}` : ''} · {a.joint ? 'Joint: ' : ''}{owners(a)}</span></li>
    {/each}
  </ul>
</div>

{#if unplaced.length > 0 && active.length > 0}
  <div class="card">
    <h2>Where does your pay land?</h2>
    <fieldset class="bare" disabled={linking}>
      {#each unplaced as i (i.id)}
        {@const choices = options(i)}
        <label for={`into-${i.id}`}>Which account is {i.name} paid into?</label>
        <select id={`into-${i.id}`} value="" onchange={(e) => paidInto(i, e.currentTarget.value)}>
          <option value="">Not sure yet</option>
          {#each choices as a (a.id)}<option value={a.id}>{a.nickname}</option>{/each}
        </select>
        {#if choices.length === 0}<p class="hint">None of the accounts above belong to the person who receives this income. Add one they own or share.</p>{/if}
      {/each}
    </fieldset>
  </div>
{/if}

<div class="card">
  <h2>Add an account</h2>
  {#if loaded}<div oninput={() => (dirty = true)} onchange={() => (dirty = true)}>{#key addKey}<AccountForm bind:this={form} {people} onsubmit={add} />{/key}</div>{:else}<p class="hint">Loading…</p>{/if}
</div>
