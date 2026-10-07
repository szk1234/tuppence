<script lang="ts">
  import { untrack } from 'svelte'
  import { changes, parseOptionalInt, parseOptionalPercent } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import type { Debt, Person } from '../../lib/types'
  import Notice from '../Notice.svelte'
  import MoneyInput from './MoneyInput.svelte'

  let { people, initial = null, disabled = false, submitLabel, onsubmit, oncancel }: {
    people: Person[]; initial?: Debt | null; disabled?: boolean; submitLabel?: string
    onsubmit: (payload: Record<string, unknown>) => Promise<boolean>; oncancel?: () => void
  } = $props()
  const uid = $props.id()

  const KINDS = {
    personal_loan: 'Personal loan', car_finance_pcp: 'Car finance (PCP)', car_finance_hp: 'Car finance (HP)', mortgage: 'Mortgage',
    student_loan: 'Student loan', bnpl: 'Buy now, pay later', overdraft: 'Overdraft', informal: 'Money owed to or by someone you know', other: 'Other',
  }
  const PLANS = { plan1: 'Plan 1', plan2: 'Plan 2', plan4: 'Plan 4', plan5: 'Plan 5', postgraduate: 'Postgraduate loan' }
  const RATE_TYPES = { fixed: 'Fixed', tracker: 'Tracker', variable: 'Variable', svr: 'Standard variable rate (SVR)' }
  const ALLOWED: Record<string, string[]> = {
    car_finance_pcp: ['agreement_start', 'via_broker', 'balloon', 'total_payable', 'annual_mileage'],
    car_finance_hp: ['agreement_start', 'via_broker', 'total_payable'],
    mortgage: ['fixed_until', 'rate_type'],
    informal: ['direction'],
  }
  const d = untrack(() => initial)
  const det = (d?.details ?? {}) as Record<string, unknown>

  let kind = $state(d?.kind ?? 'personal_loan')
  let lender = $state(d?.lender ?? '')
  let personId = $state(d?.person_id ?? '')
  let balance = $state(d?.balance ?? '')
  let apr = $state(d?.apr != null ? String(d.apr) : '')
  let payment = $state(d?.monthly_payment ?? '')
  let endDate = $state(d?.end_date ?? '')
  let plan = $state(d?.student_loan_plan ?? '')
  let agreementStart = $state(typeof det.agreement_start === 'string' ? det.agreement_start : '')
  let viaBroker = $state(det.via_broker === true ? 'yes' : det.via_broker === false ? 'no' : '')
  let balloon = $state(typeof det.balloon === 'string' ? det.balloon : '')
  let totalPayable = $state(typeof det.total_payable === 'string' ? det.total_payable : '')
  let mileage = $state(det.annual_mileage != null ? String(det.annual_mileage) : '')
  let fixedUntil = $state(typeof det.fixed_until === 'string' ? det.fixed_until : '')
  let rateType = $state(typeof det.rate_type === 'string' ? det.rate_type : '')
  let direction = $state(typeof det.direction === 'string' ? det.direction : '')
  let problem = $state('')
  let saving = $state(false)

  const isCar = $derived(kind === 'car_finance_pcp' || kind === 'car_finance_hp')

  function money(text: string, what: string): string | null | undefined {
    if (text.trim() === '') return null
    const p = parsePoundsInput(text)
    if (p === null) { problem = `Enter ${what} like 1450 or 1,450.50.`; return undefined }
    return p
  }

  /** The details this kind keeps, as the API expects; null means "not given". */
  function detailValues(): Record<string, unknown> | undefined {
    const out: Record<string, unknown> = {}
    for (const key of ALLOWED[kind] ?? []) {
      if (key === 'agreement_start') out[key] = agreementStart || null
      else if (key === 'via_broker') out[key] = viaBroker === '' ? null : viaBroker === 'yes'
      else if (key === 'balloon') { const v = money(balloon, 'the balloon payment'); if (v === undefined) return; out[key] = v }
      else if (key === 'total_payable') { const v = money(totalPayable, 'the total amount payable'); if (v === undefined) return; out[key] = v }
      else if (key === 'annual_mileage') {
        const m = parseOptionalInt(mileage, 1, 999_999)
        if (!m.ok) { problem = 'Enter the annual mileage as a whole number of miles.'; return }
        out[key] = m.value
      } else if (key === 'fixed_until') out[key] = fixedUntil || null
      else if (key === 'rate_type') out[key] = rateType || null
      else if (key === 'direction') out[key] = direction || null
    }
    return out
  }

  async function submit(e: SubmitEvent) {
    e.preventDefault(); problem = ''
    if (!lender.trim()) { problem = 'Enter who the debt is with.'; return }
    const bal = parsePoundsInput(balance)
    if (bal === null) { problem = 'Enter the current balance like 1450 or 1,450.50.'; return }
    const rate = parseOptionalPercent(apr)
    if (!rate.ok) { problem = 'Enter the interest rate as a percentage like 6.9.'; return }
    const pay = money(payment, 'the monthly payment')
    if (pay === undefined) return
    if (kind === 'informal' && !direction) { problem = 'Say whether you owe this or are owed it.'; return }
    const details = detailValues()
    if (details === undefined) return

    const body: Record<string, unknown> = {
      kind, lender: lender.trim(), person_id: personId || null, balance: bal, apr: rate.value, monthly_payment: pay, end_date: endDate || null,
    }
    if (kind === 'student_loan') body.student_loan_plan = plan || null
    saving = true
    try {
      if (!d) {
        const given = Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null))
        const keep = Object.fromEntries(Object.entries(details).filter(([, v]) => v !== null))
        await onsubmit(Object.keys(keep).length ? { ...given, details: keep } : given)
        return
      }
      const before: Record<string, unknown> = {
        kind: d.kind, lender: d.lender, person_id: d.person_id, balance: d.balance, apr: d.apr, monthly_payment: d.monthly_payment, end_date: d.end_date,
      }
      if (kind === 'student_loan') before.student_loan_plan = d.student_loan_plan
      const diff = changes(before, body)
      // Only changed details go over the wire; null clears one.
      const sentDetails: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(details)) {
        const old = det[key] ?? null
        if (JSON.stringify(old) !== JSON.stringify(value)) sentDetails[key] = value
      }
      if (Object.keys(sentDetails).length) diff.details = sentDetails
      await onsubmit(diff)
    } finally { saving = false }
  }
</script>

<form class="stack" onsubmit={submit} novalidate aria-busy={saving}>
  <Notice message={problem} />
  <fieldset class="bare" disabled={saving || disabled}>
    <label for={`${uid}-kind`}>Type of debt</label>
    <select id={`${uid}-kind`} bind:value={kind}>
      {#each Object.entries(KINDS) as [value, text]}<option {value}>{text}</option>{/each}
    </select>

    <label for={`${uid}-lender`}>Lender</label>
    <input id={`${uid}-lender`} maxlength="60" bind:value={lender} />

    <label for={`${uid}-person`}>Whose debt is it? (optional)</label>
    <select id={`${uid}-person`} bind:value={personId}>
      <option value="">Shared or not sure</option>
      {#each people as p (p.id)}<option value={p.id}>{p.display_name}</option>{/each}
    </select>

    <MoneyInput label="Current balance" bind:value={balance} />

    <label for={`${uid}-apr`}>Interest rate (APR %)</label>
    <input id={`${uid}-apr`} inputmode="decimal" bind:value={apr} />

    <MoneyInput label="Monthly payment (optional)" bind:value={payment} />

    <label for={`${uid}-end`}>Ends on (optional)</label>
    <input id={`${uid}-end`} type="date" bind:value={endDate} />

    {#if kind === 'student_loan'}
      <label for={`${uid}-plan`}>Student loan plan</label>
      <select id={`${uid}-plan`} bind:value={plan}>
        <option value="">Not sure</option>
        {#each Object.entries(PLANS) as [value, text]}<option {value}>{text}</option>{/each}
      </select>
    {/if}

    {#if isCar}
      <fieldset>
        <legend>Car finance details</legend>
        <label for={`${uid}-start`}>Agreement start date</label>
        <input id={`${uid}-start`} type="date" bind:value={agreementStart} />
        <label for={`${uid}-broker`}>Arranged through a broker or dealer</label>
        <select id={`${uid}-broker`} bind:value={viaBroker}>
          <option value="">Not sure</option><option value="yes">Yes</option><option value="no">No</option>
        </select>
        {#if kind === 'car_finance_pcp'}<MoneyInput label="Balloon payment" bind:value={balloon} />{/if}
        <MoneyInput label="Total amount payable" bind:value={totalPayable} />
        {#if kind === 'car_finance_pcp'}
          <label for={`${uid}-miles`}>Annual mileage allowance</label>
          <input id={`${uid}-miles`} inputmode="numeric" bind:value={mileage} />
        {/if}
      </fieldset>
    {:else if kind === 'mortgage'}
      <fieldset>
        <legend>Mortgage details</legend>
        <label for={`${uid}-fixed`}>Fixed rate ends</label>
        <input id={`${uid}-fixed`} type="date" bind:value={fixedUntil} />
        <label for={`${uid}-rate`}>Rate type</label>
        <select id={`${uid}-rate`} bind:value={rateType}>
          <option value="">Not sure</option>
          {#each Object.entries(RATE_TYPES) as [value, text]}<option {value}>{text}</option>{/each}
        </select>
      </fieldset>
    {:else if kind === 'informal'}
      <fieldset>
        <legend>Who owes whom</legend>
        <label for={`${uid}-direction`}>Direction</label>
        <select id={`${uid}-direction`} bind:value={direction}>
          <option value="">Choose…</option>
          <option value="i_owe">I owe this</option>
          <option value="owed_to_me">I am owed this</option>
        </select>
      </fieldset>
    {/if}

    <button type="submit">{submitLabel ?? (d ? 'Save debt' : 'Add debt')}</button>
    {#if oncancel}<button type="button" class="link" onclick={oncancel}>Cancel</button>{/if}
  </fieldset>
</form>
