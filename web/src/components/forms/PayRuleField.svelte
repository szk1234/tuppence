<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../../lib/api'
  import { ukDate } from '../../lib/dates'
  import { link } from '../../lib/router.svelte'
  import type { PayRule } from '../../lib/types'

  let { rule = $bindable(null) }: { rule?: PayRule | null } = $props()
  const uid = $props.id()

  const FREQUENCIES = {
    weekly: 'Every week', fortnightly: 'Every 2 weeks', four_weekly: 'Every 4 weeks',
    monthly_day: 'On a set day each month', last_working_day: 'Last working day of the month', last_weekday: 'Last given weekday of the month',
  }
  const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  const ADJUST = {
    previous_working_day: 'Pay me on the working day before',
    next_working_day: 'Pay me on the working day after',
    none: 'Pay me on that day anyway',
  }

  const start = untrack(() => rule) ?? {}
  let frequency = $state(typeof start.type === 'string' ? start.type : '')
  let anchor = $state(typeof start.anchor === 'string' ? start.anchor : '')
  let day = $state(typeof start.day === 'number' ? String(start.day) : '')
  let adjust = $state(typeof start.adjust === 'string' ? start.adjust : 'previous_working_day')
  let weekday = $state(typeof start.weekday === 'number' ? String(start.weekday) : '')

  const built = $derived.by((): PayRule | null => {
    if (frequency === 'weekly' || frequency === 'fortnightly' || frequency === 'four_weekly') return anchor ? { type: frequency, anchor } : null
    if (frequency === 'monthly_day') {
      const n = Number(day)
      return /^\d{1,2}$/.test(day.trim()) && n >= 1 && n <= 31 ? { type: 'monthly_day', day: n, adjust } : null
    }
    if (frequency === 'last_working_day') return { type: 'last_working_day' }
    if (frequency === 'last_weekday') return weekday === '' ? null : { type: 'last_weekday', weekday: Number(weekday) }
    return null
  })

  $effect(() => { rule = built })

  let dates = $state<string[]>([])
  let assumed = $state(false)
  let previewError = $state('')
  let ticket = 0

  $effect(() => {
    const r = built
    const mine = ++ticket
    if (!r) { dates = []; previewError = ''; return }
    const timer = setTimeout(async () => {
      try {
        const res = await api<{ next_dates: string[]; calendar_assumed?: boolean }>('/api/income/preview-rule', { method: 'POST', body: { pay_rule: r } })
        if (mine !== ticket) return
        dates = Array.isArray(res?.next_dates) ? res.next_dates : []
        assumed = res?.calendar_assumed === true
        previewError = ''
      } catch {
        if (mine !== ticket) return
        dates = []; previewError = "Can't preview those paydays yet."
      }
    }, 200)
    return () => clearTimeout(timer)
  })
</script>

<div class="payrule">
  <label for={`${uid}-freq`}>How often are you paid?</label>
  <select id={`${uid}-freq`} bind:value={frequency}>
    <option value="">Choose…</option>
    {#each Object.entries(FREQUENCIES) as [value, text]}<option {value}>{text}</option>{/each}
  </select>

  {#if frequency === 'weekly' || frequency === 'fortnightly' || frequency === 'four_weekly'}
    <label for={`${uid}-anchor`}>A recent payday</label>
    <input id={`${uid}-anchor`} type="date" bind:value={anchor} />
    <span class="hint">Any payday from the last few weeks; we count on from there.</span>
  {:else if frequency === 'monthly_day'}
    <label for={`${uid}-day`}>Day of the month (1 to 31)</label>
    <input id={`${uid}-day`} inputmode="numeric" maxlength="2" bind:value={day} />
    <label for={`${uid}-adjust`}>If it's a weekend or bank holiday</label>
    <select id={`${uid}-adjust`} bind:value={adjust}>
      {#each Object.entries(ADJUST) as [value, text]}<option {value}>{text}</option>{/each}
    </select>
  {:else if frequency === 'last_weekday'}
    <label for={`${uid}-weekday`}>Which weekday?</label>
    <select id={`${uid}-weekday`} bind:value={weekday}>
      <option value="">Choose…</option>
      {#each WEEKDAYS as text, i}<option value={String(i)}>{text}</option>{/each}
    </select>
  {/if}

  {#if dates.length}
    <p class="preview">Next paydays: {dates.map(ukDate).join(', ')}</p>
  {:else if previewError}<p class="hint">{previewError}</p>{/if}
  {#if dates.length && assumed}
    <p class="hint"><span>Set your nation for accurate bank holidays.</span> <a href="/settings/household" onclick={link}>Open Household</a></p>
  {/if}
</div>
