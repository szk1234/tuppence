<script lang="ts">
  import { onMount } from 'svelte'
  import AccountForm from '../../components/forms/AccountForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText, isConflict } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import type { Account, Person } from '../../lib/types'

  const KINDS = { current: 'Current account', savings: 'Savings account', credit_card: 'Credit card' }
  let people = $state<Person[]>([])
  let accounts = $state<Account[]>([])
  let loaded = $state(false)
  let editing = $state<string | null>(null)
  let error = $state('')
  let saved = $state('')
  let addKey = $state(0)

  const open = $derived(accounts.filter((a) => a.status === 'active'))
  const closed = $derived(accounts.filter((a) => a.status === 'closed'))
  const owners = (a: Account) => a.owner_ids.map((id) => people.find((p) => p.id === id)?.display_name ?? 'Someone who has left').join(', ')

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    accounts = (await api<{ accounts: Account[] }>('/api/accounts?include_closed=true')).accounts
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
      const a = await api<Account>('/api/accounts', { method: 'POST', body })
      accounts = [...accounts, a]; saved = `${a.nickname} added.`; addKey += 1
      return true
    } catch (err) { fail(err); return false }
  }

  async function save(a: Account, changes: Record<string, unknown>): Promise<boolean> {
    error = ''; saved = ''
    try {
      const updated = await api<Account>(`/api/accounts/${a.id}`, { method: 'PATCH', body: { changes, expected_version: a.version } })
      accounts = accounts.map((x) => (x.id === a.id ? updated : x)); editing = null; saved = `${updated.nickname} saved.`
      return true
    } catch (err) { fail(err); return false }
  }

  async function setStatus(a: Account, action: 'close' | 'reopen') {
    error = ''; saved = ''
    try {
      const updated = await api<Account>(`/api/accounts/${a.id}/${action}`, { method: 'POST', body: { expected_version: a.version } })
      accounts = accounts.map((x) => (x.id === a.id ? updated : x))
      saved = action === 'close' ? `${a.nickname} closed.` : `${a.nickname} reopened.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Accounts</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <div class="card">
    <h2>Your accounts</h2>
    {#if !loaded}<p class="hint">Loading…</p>
    {:else if open.length === 0}<p>No accounts yet.</p>{/if}
    <ul class="items">
      {#each open as a (a.id)}
        <li>
          {#if editing === a.id}
            <AccountForm {people} initial={a} onsubmit={(c) => save(a, c)} oncancel={() => (editing = null)} />
          {:else}
            <span class="name">{a.nickname}</span>
            <span class="meta">{a.provider_name} · {KINDS[a.kind]}{a.last4 ? ` · ending ${a.last4}` : ''} · {a.joint ? 'Joint: ' : ''}{owners(a)}{a.credit_limit ? ` · limit ${formatGBP(a.credit_limit)}` : ''}</span>
            <span class="actions">
              <button class="link" onclick={() => (editing = a.id)} aria-label={`Edit ${a.nickname}`}>Edit</button>
              <button class="link" onclick={() => setStatus(a, 'close')} aria-label={`Close ${a.nickname}`}>Close</button>
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if closed.length}
    <div class="card">
      <h2>Closed</h2>
      <ul class="items">
        {#each closed as a (a.id)}
          <li>
            <span class="name">{a.nickname}</span>
            <span class="meta">{a.provider_name} · {KINDS[a.kind]}</span>
            <span class="actions"><button class="link" onclick={() => setStatus(a, 'reopen')} aria-label={`Reopen ${a.nickname}`}>Reopen</button></span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if editing === null}
    <div class="card">
      <h2>Add an account</h2>
      {#if loaded}{#key addKey}<AccountForm {people} onsubmit={add} />{/key}{:else}<p class="hint">Loading…</p>{/if}
    </div>
  {/if}
</section>
