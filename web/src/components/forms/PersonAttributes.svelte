<script lang="ts">
  import { onMount } from 'svelte'
  import { api } from '../../lib/api'
  import { todayISO } from '../../lib/dates'
  import { errorText } from '../../lib/form'
  import { PERSON_ATTRIBUTES } from '../../lib/profile'
  import { valueOn, type Person, type TimelineEntry } from '../../lib/types'

  /** Work status and income band for one person, recorded on the timeline from today (a change, not an overwrite). */
  let { person, disabled = false, onsaved, onerror }: {
    person: Person; disabled?: boolean; onsaved?: (message: string) => void; onerror?: (message: string) => void
  } = $props()

  let entries = $state<TimelineEntry[]>([])
  let loaded = $state(false)
  let saving = $state(false)
  const current = (attribute: string) => (valueOn(entries, attribute, todayISO()) as string | null) ?? ''

  async function load() {
    const res = await api<{ entries: TimelineEntry[] }>(`/api/household/timeline?subject_type=person&subject_id=${encodeURIComponent(person.id)}`)
    entries = Array.isArray(res?.entries) ? res.entries : []
    loaded = true
  }
  onMount(() => { load().catch((err) => onerror?.(errorText(err))) })

  async function set(attribute: string, select: HTMLSelectElement) {
    const value = select.value
    if (!value || value === current(attribute)) { select.value = current(attribute); return }
    saving = true
    try {
      await api('/api/household/timeline', {
        method: 'POST',
        body: {
          subject_type: 'person', subject_id: person.id, attribute, value, valid_from: todayISO(),
          expected_current: valueOn(entries, attribute, todayISO()),
        },
      })
      await load()
      onsaved?.(`${person.display_name}: ${PERSON_ATTRIBUTES[attribute].label.toLowerCase()} saved.`)
    } catch (err) {
      onerror?.(errorText(err))
      await load().catch(() => {})
    } finally {
      saving = false
      select.value = current(attribute) // always show what is really stored (after a failure, not the choice that failed)
    }
  }
</script>

<fieldset class="bare" disabled={disabled || !loaded || saving}>
  <label for={`ws-${person.id}`}>Work status for {person.display_name}</label>
  <select id={`ws-${person.id}`} value={current('employment_status')} onchange={(e) => set('employment_status', e.currentTarget)}>
    <option value="" disabled>Choose…</option>
    {#each Object.entries(PERSON_ATTRIBUTES.employment_status.options ?? {}) as [value, text]}<option {value}>{text}</option>{/each}
  </select>
  <label for={`ib-${person.id}`}>Income band for {person.display_name} (optional)</label>
  <select id={`ib-${person.id}`} value={current('income_band')} onchange={(e) => set('income_band', e.currentTarget)}>
    <option value="" disabled>Choose…</option>
    {#each Object.entries(PERSON_ATTRIBUTES.income_band.options ?? {}) as [value, text]}<option {value}>{text}</option>{/each}
  </select>
  <p class="hint">Why ask? Your income band helps Tuppence check which tax, benefit and allowance rules might apply. Choose "Prefer not to say" if you'd rather not; changes apply from today and earlier answers stay in the Timeline.</p>
</fieldset>
