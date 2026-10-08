<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from './Notice.svelte'
  import { api, ApiError } from '../lib/api'
  import { answerAccount, changeAccount, type NewAccount, type StatementView } from '../lib/statements'

  type Account = { id: string; nickname: string; provider_name: string; kind: string; last4: string | null }
  type Provider = { id: string; name: string; kinds: string[] }
  type Person = { id: string; display_name: string; role: string }

  // `change` is the "Wrong account?" flow: the file is read again for the account chosen here.
  let { statement, onanswered = () => {}, change = false }: {
    statement: StatementView
    onanswered?: (updated: StatementView) => void
    change?: boolean
  } = $props()

  const KINDS: Record<string, string> = { current: 'Current account', savings: 'Savings account', credit_card: 'Credit card' }
  const id = (part: string) => `${part}-${statement.id}`

  let accounts = $state<Account[]>([])
  let providers = $state<Provider[]>([])
  let people = $state<Person[]>([])
  let choice = $state('')
  let provider = $state('other')
  let providerName = $state('')
  let kind = $state('current')
  let nickname = $state('')
  let last4 = $state('')
  let owners = $state<string[]>([])
  let error = $state('')
  let busy = $state(false)
  let loaded = $state(false)

  onMount(async () => {
    try {
      const [a, p, h] = await Promise.all([
        api<{ accounts: Account[] }>('/api/accounts'),
        api<{ providers: Provider[] }>('/api/accounts/providers'),
        api<{ people: Person[] }>('/api/household/people'),
      ])
      accounts = a.accounts
      providers = p.providers
      people = h.people
      const q = statement.question
      const prefill = q?.prefill
      if (!choice) choice = q?.best_guess ?? (change ? (statement.account_id ?? '') : accounts.length ? '' : 'new')
      provider = prefill?.provider && providers.some((x) => x.id === prefill.provider) ? prefill.provider : 'other'
      kind = prefill?.kind ?? 'current'
      nickname = prefill?.nickname ?? ''
      last4 = prefill?.last4 ?? ''
      owners = people.filter((x) => x.role === 'adult').map((x) => x.id)
      loaded = true
    } catch (err) {
      error = err instanceof ApiError ? err.detail : 'Your accounts could not be loaded.'
    }
  })

  async function submit(event: SubmitEvent) {
    event.preventDefault()
    error = ''
    if (!choice) {
      error = 'Choose one of your accounts, or add a new one.'
      return
    }
    busy = true
    try {
      const newAccount: NewAccount = {
        provider, provider_name: provider === 'other' ? providerName : null, kind, nickname,
        last4: last4 || null, owner_ids: owners,
      }
      const body = choice === 'new'
        ? { new_account: newAccount, expected_version: statement.version }
        : { account_id: choice, expected_version: statement.version }
      onanswered(await (change ? changeAccount : answerAccount)(statement.id, body))
    } catch (err) {
      error = err instanceof ApiError ? err.detail : 'Something went wrong.'
    } finally {
      busy = false
    }
  }
</script>

<form class="question" onsubmit={submit} aria-labelledby={id('q')}>
  <h3 id={id('q')}>{change ? 'Which account is this really?' : 'Which account is this?'}</h3>
  {#if change}
    <p class="hint">Tuppence will read {statement.filename} again for the account you choose. If it needs your AI model, that may cost another read.</p>
  {:else if statement.question}<p class="hint">{statement.question.reason}</p>{/if}
  <fieldset disabled={!loaded || busy}>
    <legend class="visually-hidden">Account for {statement.filename}</legend>
    {#each accounts as account (account.id)}
      <label class="choice">
        <input type="radio" name={id('account')} value={account.id} bind:group={choice} />
        {account.nickname} · {account.provider_name}{account.last4 ? ` ending ${account.last4}` : ''}
      </label>
    {/each}
    <label class="choice"><input type="radio" name={id('account')} value="new" bind:group={choice} /> + New account</label>
  </fieldset>
  {#if choice === 'new'}
    <fieldset class="new-account" disabled={!loaded || busy}>
      <legend class="visually-hidden">New account details</legend>
      <label for={id('provider')}>Bank or card provider</label>
      <select id={id('provider')} bind:value={provider}>
        {#each providers as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
      </select>
      {#if provider === 'other'}
        <label for={id('provider-name')}>Provider name</label>
        <input id={id('provider-name')} required maxlength="60" bind:value={providerName} />
      {/if}
      <label for={id('kind')}>Account type</label>
      <select id={id('kind')} bind:value={kind}>
        {#each Object.entries(KINDS) as [value, label]}<option {value}>{label}</option>{/each}
      </select>
      <label for={id('nickname')}>Nickname</label>
      <input id={id('nickname')} required maxlength="40" bind:value={nickname} />
      <label for={id('last4')}>Last 4 digits</label>
      <input id={id('last4')} inputmode="numeric" maxlength="4" pattern={'[0-9]{4}'} bind:value={last4} />
      <fieldset>
        <legend>Whose account is it?</legend>
        {#each people as person (person.id)}
          <label class="choice"><input type="checkbox" value={person.id} bind:group={owners} /> {person.display_name}</label>
        {/each}
      </fieldset>
    </fieldset>
  {/if}
  <Notice message={error} />
  <button type="submit" disabled={!loaded || busy}>Use this account</button>
</form>

<style>
  .question { border-top: 1px solid var(--line); margin-top: .75rem; padding-top: .5rem; }
  fieldset { border: none; min-width: 0; padding: 0; margin: .5rem 0; }
  .choice { display: flex; gap: .5rem; align-items: center; font-weight: 400; margin: .3rem 0; }
  .new-account { margin-left: 0; border-left: 3px solid var(--line); padding-left: .75rem; }
</style>
