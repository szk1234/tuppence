<script lang="ts">
  import { onMount, untrack } from 'svelte'
  import { api } from '../../lib/api'
  import { changes, errorText, parseOptionalInt, parseOptionalPercent } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import type { Account, Person, Provider } from '../../lib/types'
  import Notice from '../Notice.svelte'
  import MoneyInput from './MoneyInput.svelte'

  let { people, initial = null, disabled = false, submitLabel, onsubmit, oncancel }: {
    people: Person[]; initial?: Account | null; disabled?: boolean; submitLabel?: string
    onsubmit: (payload: Record<string, unknown>) => Promise<boolean>; oncancel?: () => void
  } = $props()
  const uid = $props.id()

  const KINDS = { current: 'Current account', savings: 'Savings account', credit_card: 'Credit card' }
  const a = untrack(() => initial)

  let providers = $state<Provider[]>([])
  let ready = $state(false)
  let loadError = $state('')
  let showAll = $state(false)
  let kind = $state<Account['kind']>(a?.kind ?? 'current')
  let provider = $state(a?.provider ?? '')
  let providerName = $state(a?.provider === 'other' ? a.provider_name : '')
  let nickname = $state(a?.nickname ?? '')
  let last4 = $state(a?.last4 ?? '')
  let owners = $state<string[]>(a ? [...a.owner_ids] : untrack(() => (people.length === 1 ? [people[0].id] : [])))
  let creditLimit = $state(a?.credit_limit ?? '')
  let purchaseApr = $state(a?.purchase_apr != null ? String(a.purchase_apr) : '')
  let promoApr = $state(a?.promo_apr != null ? String(a.promo_apr) : '')
  let promoEnd = $state(a?.promo_end ?? '')
  let statementDay = $state(a?.statement_day != null ? String(a.statement_day) : '')
  let problem = $state('')
  let saving = $state(false)

  onMount(async () => {
    try {
      const res = await api<{ providers: Provider[] }>('/api/accounts/providers')
      providers = Array.isArray(res?.providers) ? res.providers : []
      ready = true
    } catch (err) { loadError = errorText(err) }
  })

  // The provider's kinds are only a hint for the list; "Show all providers" never blocks a choice.
  const shown = $derived(
    providers.filter((p) => showAll || p.kinds.includes(kind) || p.id === provider),
  )

  function toggleOwner(id: string, on: boolean) {
    owners = on ? [...owners, id] : owners.filter((x) => x !== id)
  }

  function snapshot(): Record<string, unknown> | string {
    if (!provider) return 'Choose a provider.'
    if (provider === 'other' && !providerName.trim()) return 'Enter the provider name.'
    if (!nickname.trim()) return 'Enter a nickname.'
    if (last4 && !/^\d{4}$/.test(last4)) return 'Enter exactly 4 digits, or leave this blank.'
    if (owners.length === 0) return 'Choose at least one owner.'
    const body: Record<string, unknown> = {
      provider, kind, nickname: nickname.trim(), last4: last4 || null,
      owner_ids: [...owners].sort(),
    }
    if (provider === 'other') body.provider_name = providerName.trim()
    if (kind === 'credit_card') {
      const limit = creditLimit.trim() === '' ? null : parsePoundsInput(creditLimit)
      if (creditLimit.trim() !== '' && limit === null) return 'Enter the credit limit like 2500 or 2,500.'
      const purchase = parseOptionalPercent(purchaseApr), promo = parseOptionalPercent(promoApr)
      if (!purchase.ok) return 'Enter the purchase APR as a percentage like 22.9.'
      if (!promo.ok) return 'Enter the promotional APR as a percentage like 0.'
      const day = parseOptionalInt(statementDay, 1, 31)
      if (!day.ok) return 'Enter a statement day from 1 to 31.'
      Object.assign(body, {
        credit_limit: limit, purchase_apr: purchase.value, promo_apr: promo.value,
        promo_end: promoEnd || null, statement_day: day.value,
      })
    }
    return body
  }

  function before(): Record<string, unknown> {
    if (!a) return {}
    const out: Record<string, unknown> = {
      provider: a.provider, kind: a.kind, nickname: a.nickname, last4: a.last4, owner_ids: [...a.owner_ids].sort(),
      credit_limit: a.credit_limit, purchase_apr: a.purchase_apr, promo_apr: a.promo_apr, promo_end: a.promo_end, statement_day: a.statement_day,
    }
    if (a.provider === 'other') out.provider_name = a.provider_name
    return out
  }

  async function submit(e: SubmitEvent) {
    e.preventDefault(); problem = ''
    const body = snapshot()
    if (typeof body === 'string') { problem = body; return }
    saving = true
    try {
      const payload = a ? changes(before(), body) : Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null))
      await onsubmit(payload)
    } finally { saving = false }
  }
</script>

<form class="stack" onsubmit={submit} novalidate aria-busy={saving || !ready}>
  <Notice message={loadError} />
  <Notice message={problem} />
  <fieldset class="bare" disabled={saving || !ready || disabled}>
    <label for={`${uid}-kind`}>Account type</label>
    <select id={`${uid}-kind`} bind:value={kind}>
      {#each Object.entries(KINDS) as [value, text]}<option {value}>{text}</option>{/each}
    </select>

    <label for={`${uid}-provider`}>Provider</label>
    <select id={`${uid}-provider`} bind:value={provider}>
      <option value="">Choose…</option>
      {#each shown as p}<option value={p.id}>{p.name}</option>{/each}
    </select>
    <div class="toggle"><label><input type="checkbox" bind:checked={showAll} /> Show all providers</label></div>

    {#if provider === 'other'}
      <label for={`${uid}-pname`}>Provider name</label>
      <input id={`${uid}-pname`} maxlength="60" bind:value={providerName} />
    {/if}

    <label for={`${uid}-nick`}>Nickname</label>
    <input id={`${uid}-nick`} maxlength="40" placeholder="e.g. Joint bills" bind:value={nickname} />

    <label for={`${uid}-last4`}>Last 4 digits (optional)</label>
    <input id={`${uid}-last4`} inputmode="numeric" maxlength="4" autocomplete="off" bind:value={last4} />
    <p class="hint">We never ask for full account numbers or sort codes.</p>

    <fieldset>
      <legend>Owners</legend>
      {#each people as p (p.id)}
        <div class="toggle"><label><input type="checkbox" checked={owners.includes(p.id)} onchange={(e) => toggleOwner(p.id, e.currentTarget.checked)} /> {p.display_name}</label></div>
      {/each}
      {#if people.length === 0}<p class="hint">Add the people in your household first.</p>{/if}
    </fieldset>

    {#if kind === 'credit_card'}
      <fieldset>
        <legend>Card details</legend>
        <MoneyInput label="Credit limit" bind:value={creditLimit} />
        <label for={`${uid}-apr`}>Purchase APR (%)</label>
        <input id={`${uid}-apr`} inputmode="decimal" bind:value={purchaseApr} />
        <label for={`${uid}-promo`}>Promotional APR (%)</label>
        <input id={`${uid}-promo`} inputmode="decimal" bind:value={promoApr} />
        <label for={`${uid}-promoend`}>Promotional rate ends</label>
        <input id={`${uid}-promoend`} type="date" bind:value={promoEnd} />
        <label for={`${uid}-stmt`}>Statement day (1 to 31)</label>
        <input id={`${uid}-stmt`} inputmode="numeric" maxlength="2" bind:value={statementDay} />
      </fieldset>
    {/if}

    <button type="submit">{submitLabel ?? (a ? 'Save account' : 'Add account')}</button>
    {#if oncancel}<button type="button" class="link" onclick={oncancel}>Cancel</button>{/if}
  </fieldset>
</form>
