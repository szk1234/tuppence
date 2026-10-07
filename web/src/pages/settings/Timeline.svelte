<script lang="ts">
  import { onMount } from 'svelte'
  import MoneyInput from '../../components/forms/MoneyInput.svelte'
  import Notice from '../../components/Notice.svelte'
  import { api } from '../../lib/api'
  import { todayISO, ukDate } from '../../lib/dates'
  import { errorText, isConflict, parseOptionalInt } from '../../lib/form'
  import { parsePoundsInput } from '../../lib/money'
  import { HOUSEHOLD_ATTRIBUTES, HOUSEHOLD_ID, PERSON_ATTRIBUTES, showValue, type AttrDef } from '../../lib/profile'
  import { valueOn, type Person, type TimelineEntry } from '../../lib/types'

  type Subject = { type: 'household' | 'person'; id: string; label: string }
  let people = $state<Person[]>([])
  let history = $state<Record<string, TimelineEntry[]>>({})
  let loaded = $state(false)
  let saving = $state(false)
  let error = $state('')
  let saved = $state('')

  let subjectKey = $state(`household:${HOUSEHOLD_ID}`)
  let attribute = $state('')
  let value = $state('')
  let from = $state(todayISO())

  const subjects = $derived<Subject[]>([
    { type: 'household', id: HOUSEHOLD_ID, label: 'Household' },
    ...people.filter((p) => p.role !== 'child').map((p): Subject => ({ type: 'person', id: p.id, label: p.display_name })),
  ])
  const subject = $derived(subjects.find((s) => `${s.type}:${s.id}` === subjectKey) ?? subjects[0])
  const defs = (s: Subject) => (s.type === 'household' ? HOUSEHOLD_ATTRIBUTES : PERSON_ATTRIBUTES)
  const def = $derived<AttrDef | undefined>(attribute ? defs(subject)[attribute] : undefined)

  async function loadHistory(s: Subject) {
    const res = await api<{ entries: TimelineEntry[] }>(`/api/household/timeline?subject_type=${s.type}&subject_id=${encodeURIComponent(s.id)}`)
    history[`${s.type}:${s.id}`] = Array.isArray(res?.entries) ? res.entries : []
  }

  async function load() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
    await Promise.all(subjects.map(loadHistory))
    loaded = true
  }
  onMount(() => { load().catch(fail) })

  function fail(err: unknown) {
    saved = ''; error = errorText(err)
    if (isConflict(err)) load().catch(() => {})
  }

  function changeSubject(key: string) { subjectKey = key; attribute = ''; value = '' }
  function changeAttribute(a: string) { attribute = a; value = '' }

  async function add(e: SubmitEvent) {
    e.preventDefault(); error = ''; saved = ''
    if (!def) { error = 'Choose what changed.'; return }
    if (!from) { error = 'Choose the date this applies from.'; return }
    let sent: unknown = value
    if (value.trim() === '') { error = 'Enter the new value.'; return }
    if (def.kind === 'money') {
      sent = parsePoundsInput(value)
      if (sent === null) { error = 'Enter an amount like 1450 or 1,450.50.'; return }
    } else if (def.kind === 'int') {
      const n = parseOptionalInt(value, 0, 20)
      if (!n.ok || n.value === null) { error = 'Enter a whole number from 0 to 20.'; return }
      sent = n.value
    }
    const s = subject
    saving = true
    try {
      const current = valueOn(history[`${s.type}:${s.id}`] ?? [], attribute, from)
      await api('/api/household/timeline', {
        method: 'POST',
        body: { subject_type: s.type, subject_id: s.id, attribute, value: sent, valid_from: from, expected_current: current },
      })
      await loadHistory(s)
      saved = `${def.label} updated from ${ukDate(from)}.`
      value = ''
    } catch (err) { fail(err) } finally { saving = false }
  }

  async function remove(entry: TimelineEntry, s: Subject) {
    error = ''; saved = ''
    try {
      await api(`/api/household/timeline/${entry.id}?expected_version=${entry.version}`, { method: 'DELETE' })
      await loadHistory(s)
      saved = 'Change removed.'
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Timeline</h1>
  <p class="hint">Life changes such as a new job or moving home are kept as history, with the date they started.</p>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <form class="card" onsubmit={add} novalidate aria-busy={!loaded || saving}>
    <h2>Add a change</h2>
    <fieldset class="bare" disabled={!loaded || saving}>
      <label for="tl-subject">Who is this about?</label>
      <select id="tl-subject" value={subjectKey} onchange={(e) => changeSubject(e.currentTarget.value)}>
        {#each subjects as s}<option value={`${s.type}:${s.id}`}>{s.label}</option>{/each}
      </select>
      <label for="tl-attr">What changed?</label>
      <select id="tl-attr" value={attribute} onchange={(e) => changeAttribute(e.currentTarget.value)}>
        <option value="">Choose…</option>
        {#each Object.entries(defs(subject)) as [key, d]}<option value={key}>{d.label}</option>{/each}
      </select>
      {#if def?.kind === 'choice'}
        <label for="tl-value">New value</label>
        <select id="tl-value" bind:value>
          <option value="">Choose…</option>
          {#each Object.entries(def.options ?? {}) as [key, text]}<option value={key}>{text}</option>{/each}
        </select>
      {:else if def?.kind === 'money'}
        <MoneyInput label="New value" bind:value />
      {:else if def}
        <label for="tl-value">New value</label>
        <input id="tl-value" inputmode={def.kind === 'int' ? 'numeric' : 'text'} bind:value />
      {/if}
      <label for="tl-from">From</label>
      <input id="tl-from" type="date" bind:value={from} />
      <button type="submit">Add change</button>
    </fieldset>
  </form>

  {#each subjects as s (`${s.type}:${s.id}`)}
    <div class="card">
      <h2>{s.label}</h2>
      {#if (history[`${s.type}:${s.id}`] ?? []).length === 0}<p class="hint">No changes recorded.</p>{/if}
      <ul class="items">
        {#each history[`${s.type}:${s.id}`] ?? [] as e (e.id)}
          <li>
            <span class="name">{defs(s)[e.attribute]?.label ?? e.attribute}</span>
            <span class="meta">{showValue(defs(s)[e.attribute], e.value)} · from {ukDate(e.valid_from)}{e.valid_to ? ` until ${ukDate(e.valid_to)}` : ''}</span>
            <span class="actions"><button class="link" onclick={() => remove(e, s)} aria-label={`Remove ${defs(s)[e.attribute]?.label ?? e.attribute} change from ${ukDate(e.valid_from)} for ${s.label}`}>Remove</button></span>
          </li>
        {/each}
      </ul>
    </div>
  {/each}
</section>
