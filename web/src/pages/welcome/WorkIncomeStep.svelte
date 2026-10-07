<script lang="ts">
  import { onMount } from 'svelte'
  import IncomeForm from '../../components/forms/IncomeForm.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { todayISO } from '../../lib/dates'
  import { errorText, isConflict } from '../../lib/form'
  import { formatGBP } from '../../lib/money'
  import { PERSON_ATTRIBUTES } from '../../lib/profile'
  import { valueOn, type Account, type Income, type Person, type TimelineEntry } from '../../lib/types'

  let people = $state<Person[]>([])
  let accounts = $state<Account[]>([])
  let incomes = $state<Income[]>([])
  let entries = $state<Record<string, TimelineEntry[]>>({})
  let loaded = $state(false)
  let saving = $state(false)
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
  const earners = $derived(people.filter((p) => p.role !== 'child'))
  const active = $derived(incomes.filter((i) => i.status === 'active'))
  const who = (i: Income) => people.find((p) => p.id === i.person_id)?.display_name ?? 'Someone who has left'
  const current = (pid: string, attr: string) => (valueOn(entries[pid] ?? [], attr, todayISO()) as string | null) ?? ''

  async function loadEntries(pid: string) {
    const res = await api<{ entries: TimelineEntry[] }>(`/api/household/timeline?subject_type=person&subject_id=${encodeURIComponent(pid)}`)
    entries[pid] = Array.isArray(res?.entries) ? res.entries : []
  }

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    accounts = (await api<{ accounts: Account[] }>('/api/accounts')).accounts
    incomes = (await api<{ income: Income[] }>('/api/income')).income
    await Promise.all(people.filter((p) => p.role !== 'child').map((p) => loadEntries(p.id)))
    loaded = true
  }
  onMount(() => { load().catch((err) => { error = errorText(err) }) })

  async function setAttribute(p: Person, attribute: string, value: string) {
    error = ''; saved = ''
    if (value === current(p.id, attribute)) return
    saving = true
    try {
      await api('/api/household/timeline', {
        method: 'POST',
        body: {
          subject_type: 'person', subject_id: p.id, attribute, value: value || null, valid_from: todayISO(),
          expected_current: valueOn(entries[p.id] ?? [], attribute, todayISO()),
        },
      })
      await loadEntries(p.id)
      saved = `${p.display_name}: ${PERSON_ATTRIBUTES[attribute].label.toLowerCase()} saved.`
    } catch (err) {
      error = errorText(err)
      if (isConflict(err)) await loadEntries(p.id).catch(() => {})
    } finally { saving = false }
  }

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
    <fieldset class="bare" disabled={!loaded || saving}>
      <label for={`ws-${p.id}`}>Work status for {p.display_name}</label>
      <select id={`ws-${p.id}`} value={current(p.id, 'employment_status')} onchange={(e) => setAttribute(p, 'employment_status', e.currentTarget.value)}>
        <option value="">Choose…</option>
        {#each Object.entries(PERSON_ATTRIBUTES.employment_status.options ?? {}) as [value, text]}<option {value}>{text}</option>{/each}
      </select>
      <label for={`ib-${p.id}`}>Income band for {p.display_name} (optional)</label>
      <select id={`ib-${p.id}`} value={current(p.id, 'income_band')} onchange={(e) => setAttribute(p, 'income_band', e.currentTarget.value)}>
        <option value="">Prefer not to say</option>
        {#each Object.entries(PERSON_ATTRIBUTES.income_band.options ?? {}) as [value, text]}<option {value}>{text}</option>{/each}
      </select>
      <p class="hint">Why ask? Your income band helps Tuppence check which tax, benefit and allowance rules might apply to you. Leave it blank if you'd rather not say.</p>
    </fieldset>
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
