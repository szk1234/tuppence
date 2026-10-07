<script lang="ts">
  import { untrack } from 'svelte'
  import { changes } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import type { Account, Income, PayRule, Person } from '../../lib/types'
  import Notice from '../Notice.svelte'
  import MoneyInput from './MoneyInput.svelte'
  import PayRuleField from './PayRuleField.svelte'

  let { people, accounts, initial = null, disabled = false, submitLabel, onsubmit, oncancel }: {
    people: Person[]; accounts: Account[]; initial?: Income | null; disabled?: boolean; submitLabel?: string
    onsubmit: (payload: Record<string, unknown>) => Promise<boolean>; oncancel?: () => void
  } = $props()
  const uid = $props.id()

  const KINDS = {
    salary: 'Salary', self_employment: 'Self-employment', benefits: 'Benefits', pension: 'Pension',
    rental: 'Rental income', maintenance: 'Maintenance', other: 'Other',
  }
  const COMPONENTS = { bonus: 'Bonus', overtime: 'Overtime', commission: 'Commission' }
  const i = untrack(() => initial)
  const earners = $derived(people.filter((p) => p.role !== 'child'))

  let personId = $state(i?.person_id ?? untrack(() => (earners.length === 1 ? earners[0].id : '')))
  let kind = $state(i?.kind ?? 'salary')
  let name = $state(i?.name ?? '')
  let net = $state(i?.net_amount ?? '')
  let accountId = $state(i?.account_id ?? '')
  let rule = $state<PayRule | null>(i?.pay_rule ?? null)
  let variable = $state<string[]>(i ? [...i.variable_components] : [])
  let problem = $state('')
  let saving = $state(false)

  const owned = $derived(accounts.filter((a) => a.status === 'active' && personId !== '' && a.owner_ids.includes(personId)))

  function setPerson(id: string) {
    personId = id
    if (accountId && !accounts.some((a) => a.id === accountId && a.owner_ids.includes(id))) accountId = ''
  }
  function toggle(key: string, on: boolean) { variable = on ? [...variable, key] : variable.filter((x) => x !== key) }

  async function submit(e: SubmitEvent) {
    e.preventDefault(); problem = ''
    if (!personId) { problem = 'Choose who receives this income.'; return }
    if (!name.trim()) { problem = 'Enter a name for this income.'; return }
    const amount = parsePoundsInput(net)
    if (amount === null) { problem = 'Enter the take-home amount like 1450 or 1,450.50.'; return }
    if (!rule) { problem = "Choose how often you're paid and fill in the details."; return }
    const body: Record<string, unknown> = {
      person_id: personId, kind, name: name.trim(), net_amount: amount, account_id: accountId || null,
      pay_rule: rule, variable_components: [...variable].sort(),
    }
    saving = true
    try {
      if (i) {
        const before = {
          person_id: i.person_id, kind: i.kind, name: i.name, net_amount: i.net_amount, account_id: i.account_id,
          pay_rule: i.pay_rule, variable_components: [...i.variable_components].sort(),
        }
        await onsubmit(changes(before, body))
      } else {
        if (body.account_id === null) delete body.account_id
        await onsubmit(body)
      }
    } finally { saving = false }
  }
</script>

<form class="stack" onsubmit={submit} novalidate aria-busy={saving}>
  <Notice message={problem} />
  <fieldset class="bare" disabled={saving || disabled}>
    <label for={`${uid}-person`}>Who receives this income?</label>
    <select id={`${uid}-person`} value={personId} onchange={(e) => setPerson(e.currentTarget.value)}>
      <option value="">Choose…</option>
      {#each earners as p (p.id)}<option value={p.id}>{p.display_name}</option>{/each}
    </select>

    <label for={`${uid}-kind`}>Type of income</label>
    <select id={`${uid}-kind`} bind:value={kind}>
      {#each Object.entries(KINDS) as [value, text]}<option {value}>{text}</option>{/each}
    </select>

    <label for={`${uid}-name`}>Income name</label>
    <input id={`${uid}-name`} maxlength="60" placeholder="e.g. Salary from Acme" bind:value={name} />

    <MoneyInput label="Take-home amount each time you're paid" bind:value={net} />

    <PayRuleField bind:rule />

    <label for={`${uid}-account`}>Paid into</label>
    <select id={`${uid}-account`} bind:value={accountId}>
      <option value="">Not sure yet</option>
      {#each owned as a (a.id)}<option value={a.id}>{a.nickname}</option>{/each}
    </select>

    <fieldset>
      <legend>Pay that varies (optional)</legend>
      {#each Object.entries(COMPONENTS) as [value, text]}
        <div class="toggle"><label><input type="checkbox" checked={variable.includes(value)} onchange={(e) => toggle(value, e.currentTarget.checked)} /> {text}</label></div>
      {/each}
    </fieldset>

    <button type="submit">{submitLabel ?? (i ? 'Save income' : 'Add income')}</button>
    {#if oncancel}<button type="button" class="link" onclick={oncancel}>Cancel</button>{/if}
  </fieldset>
</form>
