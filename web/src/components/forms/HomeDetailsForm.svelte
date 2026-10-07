<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../../lib/api'
  import { todayISO } from '../../lib/dates'
  import { errorText, isConflict, parseOptionalInt } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import { COUNCIL_TAX_BANDS, HOUSEHOLD_ID, TENURES } from '../../lib/profile'
  import { valueOn, type TimelineEntry } from '../../lib/types'
  import Notice from '../Notice.svelte'
  import MoneyInput from './MoneyInput.svelte'

  let { onsaved }: { onsaved?: () => void } = $props()
  const uid = $props.id()

  let entries = $state<TimelineEntry[]>([])
  let loaded = $state(false)
  let saving = $state(false)
  let error = $state('')
  let saved = $state('')
  let tenure = $state('')
  let monthly = $state('')
  let bedrooms = $state('')
  let band = $state('')
  let from = $state(todayISO())

  async function load() {
    const res = await api<{ entries: TimelineEntry[] }>(`/api/household/timeline?subject_type=household&subject_id=${HOUSEHOLD_ID}`)
    entries = Array.isArray(res?.entries) ? res.entries : []
    const today = todayISO()
    const now = (attr: string) => valueOn(entries, attr, today)
    tenure = (now('housing_tenure') as string | null) ?? ''
    monthly = (now('housing_monthly_pence') as string | null) ?? ''
    bedrooms = now('bedrooms') == null ? '' : String(now('bedrooms'))
    band = (now('council_tax_band') as string | null) ?? ''
    loaded = true
  }
  onMount(() => { load().catch((err) => { error = errorText(err) }) })

  async function submit(e: SubmitEvent) {
    e.preventDefault(); error = ''; saved = ''
    if (!from) { error = 'Choose the date these details apply from.'; return }
    const wanted: [string, unknown][] = []
    if (tenure) wanted.push(['housing_tenure', tenure])
    if (monthly.trim() !== '') {
      const p = parsePoundsInput(monthly)
      if (p === null) { error = 'Enter the monthly cost like 1450 or 1,450.50.'; return }
      wanted.push(['housing_monthly_pence', p])
    }
    const beds = parseOptionalInt(bedrooms, 0, 20)
    if (!beds.ok) { error = 'Enter bedrooms as a whole number from 0 to 20.'; return }
    if (beds.value !== null) wanted.push(['bedrooms', beds.value])
    if (band) wanted.push(['council_tax_band', band])

    saving = true
    try {
      let written = 0
      for (const [attribute, value] of wanted) {
        const current = valueOn(entries, attribute, from)
        if (current === value) continue
        await api('/api/household/timeline', {
          method: 'POST',
          body: { subject_type: 'household', subject_id: HOUSEHOLD_ID, attribute, value, valid_from: from, expected_current: current },
        })
        written += 1
      }
      await load()
      saved = written ? 'Home details saved.' : 'Nothing to change.'
      if (written) onsaved?.()
    } catch (err) {
      error = errorText(err)
      if (isConflict(err)) await load().catch(() => {})
    } finally { saving = false }
  }
</script>

<form class="card" onsubmit={submit} novalidate aria-busy={!loaded || saving}>
  <h2>Your home</h2>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />
  <fieldset class="bare" disabled={!loaded || saving}>
    <label for={`${uid}-tenure`}>Housing</label>
    <select id={`${uid}-tenure`} bind:value={tenure}>
      <option value="">Choose…</option>
      {#each Object.entries(TENURES) as [value, text]}<option {value}>{text}</option>{/each}
    </select>
    <MoneyInput label="Monthly housing cost" bind:value={monthly} hint="Rent or mortgage payment." />
    <label for={`${uid}-beds`}>Bedrooms</label>
    <input id={`${uid}-beds`} inputmode="numeric" maxlength="2" bind:value={bedrooms} />
    <label for={`${uid}-band`}>Council tax band</label>
    <select id={`${uid}-band`} bind:value={band}>
      <option value="">Not sure</option>
      {#each COUNCIL_TAX_BANDS as b}<option value={b}>Band {b}</option>{/each}
    </select>
    <label for={`${uid}-from`}>In place from</label>
    <input id={`${uid}-from`} type="date" bind:value={from} />
    <p class="hint">Moving home later? Add the new details with a new date and your history is kept.</p>
    <button type="submit">Save home details</button>
  </fieldset>
</form>
