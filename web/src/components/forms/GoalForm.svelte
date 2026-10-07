<script lang="ts">
  import { untrack } from 'svelte'
  import { changes } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import type { Goal } from '../../lib/types'
  import Notice from '../Notice.svelte'
  import MoneyInput from './MoneyInput.svelte'

  let { initial = null, suggestEmergencyFund = false, disabled = false, submitLabel, onsubmit, oncancel }: {
    initial?: Goal | null; suggestEmergencyFund?: boolean; disabled?: boolean; submitLabel?: string
    onsubmit: (payload: Record<string, unknown>) => Promise<boolean>; oncancel?: () => void
  } = $props()
  const uid = $props.id()

  const KINDS = {
    emergency_fund: 'Emergency fund', house_deposit: 'House deposit', holiday: 'Holiday', car: 'Car', wedding: 'Wedding',
    education: 'Education', retirement: 'Retirement', debt_free: 'Becoming debt free', other: 'Other',
  }
  const PRIORITIES = { '1': 'High', '2': 'Medium', '3': 'Low' }
  const g = untrack(() => initial)

  let name = $state(g?.name ?? '')
  let kind = $state(g?.kind ?? 'other')
  let target = $state(g?.target_amount ?? '')
  let saved = $state(g && g.saved_amount !== '0.00' ? g.saved_amount : '')
  let targetDate = $state(g?.target_date ?? '')
  let priority = $state(String(g?.priority ?? 2))
  let problem = $state('')
  let saving = $state(false)


  let formEl: HTMLFormElement
  let result = false
  let pending: Promise<unknown> = Promise.resolve()
  /** Submit what is typed (the wizard's Continue). False when validation or the save failed; the form shows why. */
  export async function save(): Promise<boolean> {
    formEl.requestSubmit()
    await pending
    return result
  }

  async function send(run: () => Promise<boolean>): Promise<void> {
    saving = true
    try { result = await run() } finally { saving = false }
  }

  function submit(e: SubmitEvent) { e.preventDefault(); pending = Promise.resolve(run()) }
  function run(): Promise<void> | undefined {
    result = false; problem = ''
    if (!name.trim()) { problem = 'Enter a name for this goal.'; return }
    const targetPounds = target.trim() === '' ? null : parsePoundsInput(target)
    if (target.trim() !== '' && targetPounds === null) { problem = 'Enter the target like 5000 or 5,000.'; return }
    const savedPounds = saved.trim() === '' ? '0.00' : parsePoundsInput(saved)
    if (savedPounds === null) { problem = 'Enter what you have saved so far like 250 or 1,250.50.'; return }
    const body: Record<string, unknown> = {
      name: name.trim(), kind, target_amount: targetPounds, saved_amount: savedPounds,
      target_date: targetDate || null, priority: Number(priority),
    }
    if (!g) return send(() => onsubmit(Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null))))
    const before = { name: g.name, kind: g.kind, target_amount: g.target_amount, saved_amount: g.saved_amount, target_date: g.target_date, priority: g.priority }
    return send(() => onsubmit(changes(before, body)))
  }

  const addEmergencyFund = () => send(() => onsubmit({ name: 'Emergency fund', kind: 'emergency_fund', priority: 1 }))
</script>

{#if suggestEmergencyFund && !g}
  <div class="card suggestion">
    <p>No emergency fund yet — most people aim for 3–6 months of essential spending.</p>
    <button type="button" disabled={saving || disabled} onclick={addEmergencyFund}>Add emergency fund goal</button>
  </div>
{/if}

<form class="stack" bind:this={formEl} onsubmit={submit} novalidate aria-busy={saving}>
  <Notice message={problem} />
  <fieldset class="bare" disabled={saving || disabled}>
    <label for={`${uid}-name`}>Goal name</label>
    <input id={`${uid}-name`} maxlength="60" placeholder="e.g. House deposit" bind:value={name} />

    <label for={`${uid}-kind`}>Type of goal</label>
    <select id={`${uid}-kind`} bind:value={kind}>
      {#each Object.entries(KINDS) as [value, text]}<option {value}>{text}</option>{/each}
    </select>

    <MoneyInput label="Target amount (optional)" bind:value={target} />
    <MoneyInput label="Saved so far" bind:value={saved} />

    <label for={`${uid}-date`}>Target date (optional)</label>
    <input id={`${uid}-date`} type="date" bind:value={targetDate} />

    <label for={`${uid}-priority`}>Priority</label>
    <select id={`${uid}-priority`} bind:value={priority}>
      {#each Object.entries(PRIORITIES) as [value, text]}<option {value}>{text}</option>{/each}
    </select>

    <button type="submit">{submitLabel ?? (g ? 'Save goal' : 'Add goal')}</button>
    {#if oncancel}<button type="button" class="link" onclick={oncancel}>Cancel</button>{/if}
  </fieldset>
</form>
