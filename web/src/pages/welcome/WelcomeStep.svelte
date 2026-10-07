<script lang="ts">
  import { onMount } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { errorText, isConflict } from '../../lib/form'
  import { NATIONS } from '../../lib/profile'
  import type { Household } from '../../lib/types'

  const uid = $props.id()
  let household = $state<Household | null>(null)
  let nation = $state('')
  let district = $state('')
  let error = $state('')

  onMount(async () => {
    try {
      household = await api<Household>('/api/household')
      nation = household.nation ?? ''
      district = household.postcode_district ?? ''
    } catch (err) { error = errorText(err) }
  })

  /** Called by the wizard before it records this step. Returns false to stay on the step. */
  export async function save(): Promise<boolean> {
    error = ''
    if (!household) { error = 'Still loading. Try again in a moment.'; return false }
    const changes: Record<string, string | null> = {}
    if (nation !== (household.nation ?? '')) changes.nation = nation || null
    if (district.trim() !== (household.postcode_district ?? '')) changes.postcode_district = district.trim() || null
    if (Object.keys(changes).length === 0) return true
    try {
      household = await api<Household>('/api/household', { method: 'PATCH', body: { changes, expected_version: household.version } })
      district = household.postcode_district ?? ''
      return true
    } catch (err) {
      error = errorText(err)
      // Changed elsewhere: take the stored household as the new baseline (what is typed stays), so Continue can retry.
      if (isConflict(err)) household = await api<Household>('/api/household').catch(() => household)
      return false
    }
  }
</script>

<p class="disclaimer">Tuppence gives insights and guidance, not regulated financial advice. For debt help, MoneyHelper, StepChange and Citizens Advice are free.</p>
<p>A few quick questions build your picture in about five minutes. Every step can be skipped and changed later in Settings.</p>
<Notice message={error} />
<fieldset class="bare" disabled={!household}>
  <label for={`${uid}-nation`}>Nation</label>
  <select id={`${uid}-nation`} bind:value={nation}>
    <option value="">Choose…</option>
    {#each Object.entries(NATIONS) as [value, label]}<option {value}>{label}</option>{/each}
  </select>
  <label for={`${uid}-district`}>Postcode district</label>
  <input id={`${uid}-district`} placeholder="e.g. LS6" bind:value={district} autocomplete="off" />
  <p class="hint">Only the first part of your postcode. Tuppence never needs your full address. Your nation sets bank holidays, council tax and benefit rules.</p>
</fieldset>
