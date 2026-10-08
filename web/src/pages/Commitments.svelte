<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../components/Notice.svelte'
  import { ApiError } from '../lib/api'
  import { months } from '../lib/calendar'
  import { formatGBP } from '../lib/money'
  import { dmyDate } from '../lib/dates'
  import {
    dismissCommitment, getCommitments, restoreCommitment, type Commitment, type CommitmentsView, type Due,
  } from '../lib/understanding'

  const KINDS: Record<string, string> = { bill: 'Bills', subscription: 'Subscriptions', instalment: 'Instalments' }
  let view = $state<CommitmentsView | null>(null)
  let showHidden = $state(false)
  let error = $state('')
  let busy = $state(false)
  let ready = $state(false)
  const locked = $derived(busy || !ready)

  async function load() {
    try { view = await getCommitments(showHidden) } catch (err) { error = err instanceof ApiError ? err.detail : 'Could not load commitments.' }
  }
  onMount(async () => { await load(); ready = true })

  const flagged = $derived((view?.commitments ?? []).filter((c) => !c.dismissed && c.flags.some((f) => f !== 'varies')))
  const byDay = $derived.by(() => {
    const out = new Map<string, Due[]>()
    for (const due of view?.upcoming ?? []) out.set(due.date, [...(out.get(due.date) ?? []), due])
    return out
  })

  function flagText(c: Commitment): string[] {
    const out: string[] = []
    if (c.flags.includes('price_rise') && c.price_history.length >= 2) {
      const [before, after] = c.price_history.slice(-2)
      out.push(`Price went up from ${formatGBP(before.amount)} to ${formatGBP(after.amount)} on ${dmyDate(after.since)}`)
    }
    if (c.flags.includes('lapsed')) out.push(`No payment since ${dmyDate(c.last_paid)}: cancelled, or missed?`)
    else if (c.flags.includes('missed')) out.push('A payment seems to have been missed')
    if (c.flags.includes('duplicate')) out.push(`Possible duplicate of ${c.duplicate_of.join(', ')}`)
    if (c.flags.includes('free_trial_converted')) out.push('A free or £1 trial turned into a paid plan')
    return out
  }

  async function change(action: () => Promise<unknown>) {
    if (locked) return
    error = ''
    busy = true
    try { await action(); await load() } catch (err) { error = err instanceof ApiError ? err.detail : 'Something went wrong.' } finally { busy = false }
  }
  const hide = (c: Commitment) => change(() => dismissCommitment(c.id, c.version))
  const restore = (c: Commitment) => change(() => restoreCommitment(c.id, c.version))
  async function toggleHidden() {
    if (busy) return
    busy = true
    try { await load() } finally { busy = false }
  }
</script>

<section>
  <h1>Commitments</h1>
  <p>Bills, subscriptions and instalments Tuppence found in your statements.</p>
  <Notice message={error} />
  {#if view}
    <div class="row tiles">
      <div class="card"><p class="meta">Every year</p><p class="big">{formatGBP(view.totals.annual)}</p><p class="meta">about {formatGBP(view.totals.monthly)} a month</p></div>
      {#each Object.entries(view.totals.by_kind) as [kind, amount] (kind)}
        <div class="card"><p class="meta">{KINDS[kind]}</p><p class="big">{formatGBP(amount)}</p><p class="meta">a year</p></div>
      {/each}
    </div>
    {#if flagged.length}
      <h2>Worth a look</h2>
      <ul>
        {#each flagged as c (c.id)}
          <li><strong>{c.name}</strong>: {flagText(c).join('. ')}</li>
        {/each}
      </ul>
    {/if}
    <h2>Coming up</h2>
    {#each months(view.calendar_start, view.calendar_end) as month (month.key)}
      <table class="calendar">
        <caption>{month.label}</caption>
        <thead><tr>{#each ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as d}<th scope="col">{d}</th>{/each}</tr></thead>
        <tbody>
          {#each month.weeks as week, w (w)}
            <tr>
              {#each week as day (day.iso)}
                <td class:out={!day.inMonth}>
                  {#if day.inMonth}
                    <span class="day">{day.day}</span>
                    {#each byDay.get(day.iso) ?? [] as due (due.commitment_id)}
                      <span class="due">{due.name} {formatGBP(due.amount)}</span>
                    {/each}
                  {/if}
                </td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    {/each}
    <h2>All commitments</h2>
    <div class="scroll">
      <table>
        <caption class="visually-hidden">All commitments</caption>
        <thead><tr><th scope="col">Name</th><th scope="col">How often</th><th scope="col" class="num">Amount</th><th scope="col" class="num">A year</th><th scope="col">Next due</th><th scope="col">Status</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead>
        <tbody>
          {#each view.commitments as c (c.id)}
            <tr class:hidden-row={c.dismissed}>
              <td>{c.name}<br /><span class="meta">{KINDS[c.kind]}{#each c.flag_labels as label} · {label}{/each}</span></td>
              <td>{c.cadence_label}</td>
              <td class="num">{formatGBP(c.amount)}</td>
              <td class="num">{formatGBP(c.annual_cost)}</td>
              <td>{c.next_due ? dmyDate(c.next_due) : ''}</td>
              <td>{c.status === 'active' ? 'Active' : c.status === 'lapsed' ? 'Stopped' : 'Ended'}</td>
              <td>{#if c.dismissed}<button class="link" disabled={locked} onclick={() => restore(c)}>Show again</button>{:else}<button class="link" disabled={locked} onclick={() => hide(c)} aria-label={`${c.name} is not a commitment`}>Not a commitment</button>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <label class="choice"><input type="checkbox" bind:checked={showHidden} disabled={locked} onchange={toggleHidden} /> Show the ones you hid</label>
  {/if}
</section>

<style>
  .tiles .card { min-width: 10rem; flex: 1; }
  .big { font-size: 1.5rem; font-weight: 700; margin: .1rem 0; font-variant-numeric: tabular-nums; }
  .calendar { border-collapse: collapse; width: 100%; table-layout: fixed; margin-bottom: 1rem; }
  .calendar td { border: 1px solid var(--line); vertical-align: top; height: 4.5rem; padding: .25rem; font-size: .8rem; }
  .calendar td.out { background: var(--bg); }
  .day { display: block; color: var(--muted); }
  .due { display: block; font-weight: 600; overflow-wrap: anywhere; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: .35rem .5rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  caption { text-align: left; font-weight: 600; padding: .5rem 0; }
  .hidden-row { opacity: .6; }
  .choice { display: flex; gap: .5rem; align-items: center; font-weight: 400; }
</style>
