<script lang="ts">
  import { onMount, tick } from 'svelte'
  import Notice from '../../components/Notice.svelte'
  import HomeDetailsForm from '../../components/forms/HomeDetailsForm.svelte'
  import PersonAttributes from '../../components/forms/PersonAttributes.svelte'
  import PersonForm from '../../components/forms/PersonForm.svelte'
  import { api } from '../../lib/api'
  import { errorText, isConflict } from '../../lib/form'
  import { router } from '../../lib/router.svelte'
  import type { Person } from '../../lib/types'

  type Household = { nation: string | null; postcode_district: string | null; currency: string; period_mode: string; period_anchor_person_id: string | null; version: number }

  const ROLES = { adult: 'Adult', child: 'Child', dependent_adult: 'Dependent adult' }
  const NATIONS = { england: 'England', wales: 'Wales', scotland: 'Scotland', northern_ireland: 'Northern Ireland' }

  let household = $state<Household | null>(null)
  let people = $state<Person[]>([])
  let nation = $state('')
  let district = $state('')
  let error = $state('')
  let saved = $state('')
  let loaded = $state(false)
  let editing = $state<string | null>(null)

  const fail = (err: unknown) => { saved = ''; error = errorText(err) }
  const ok = (message: string) => { error = ''; saved = message }
  const problem = (message: string) => { saved = ''; error = message }

  async function loadPeople() {
    people = (await api<{ people: Person[] }>('/api/household/people')).people
  }

  async function load() {
    household = await api<Household>('/api/household')
    nation = household.nation ?? ''
    district = household.postcode_district ?? ''
    await loadPeople()
    loaded = true
    await tick()
    showLinkedPerson()
  }
  onMount(() => { load().catch(fail) })

  /** A prompt such as "Add Kid A's birth year" links to /settings/household#person-<id>: bring that person into view. */
  function showLinkedPerson() {
    if (!router.hash.startsWith('#person-')) return
    const target = document.getElementById(router.hash.slice(1))
    if (!target) return
    target.scrollIntoView?.({ block: 'center' })
    target.focus()
  }

  async function saveHousehold(e: SubmitEvent) {
    e.preventDefault(); error = ''
    try {
      household = await api<Household>('/api/household', {
        method: 'PATCH',
        body: { changes: { nation: nation || null, postcode_district: district || null }, expected_version: household!.version },
      })
      district = household.postcode_district ?? ''
      saved = 'Household saved.'
    } catch (err) { fail(err) }
  }

  async function addPerson(body: { display_name?: string; role?: Person['role']; birth_year?: number | null }): Promise<boolean> {
    error = ''
    try {
      const p = await api<Person>('/api/household/people', { method: 'POST', body })
      people = [...people, p]
      saved = `${p.display_name} added.`
      return true
    } catch (err) { fail(err); return false }
  }

  async function savePerson(p: Person, changes: Record<string, unknown>): Promise<boolean> {
    error = ''
    try {
      const updated = await api<Person>(`/api/household/people/${p.id}`, { method: 'PATCH', body: { changes, expected_version: p.version } })
      people = people.map((x) => (x.id === p.id ? updated : x))
      editing = null
      ok(`${updated.display_name} saved.`)
      return true
    } catch (err) {
      fail(err)
      // A newer version reloads the person, and the edit form (keyed by version) starts again from what is stored.
      if (isConflict(err)) await loadPeople().catch(() => {})
      return false
    }
  }

  async function retire(p: Person) {
    error = ''
    try {
      await api(`/api/household/people/${p.id}/retire`, { method: 'POST', body: { expected_version: p.version } })
      people = people.filter((x) => x.id !== p.id)
      saved = `${p.display_name} removed from the household.`
    } catch (err) { fail(err) }
  }
</script>

<section>
  <h1>Household</h1>
  <Notice message={error} />
  <Notice message={saved} kind="ok" />

  <form class="card" onsubmit={saveHousehold} aria-busy={!loaded}>
    <h2>Where you live</h2>
    <fieldset class="bare" disabled={!loaded}>
    <label for="hh-nation">Nation</label>
    <select id="hh-nation" bind:value={nation}>
      <option value="">Choose…</option>
      {#each Object.entries(NATIONS) as [value, label]}<option {value}>{label}</option>{/each}
    </select>
    <label for="hh-district">Postcode district</label>
    <input id="hh-district" placeholder="e.g. LS6" bind:value={district} autocomplete="off" />
    <p class="hint">Only the first part of your postcode. Tuppence never needs your full address.</p>
    <button type="submit">Save household</button>
    </fieldset>
  </form>

  <div class="card">
    <h2>People</h2>
    {#if loaded && people.length === 0}<p>No one added yet.</p>{/if}
    <ul class="items">
      {#each people as p (p.id)}
        <li id={`person-${p.id}`} tabindex="-1">
          {#if editing === p.id}
            {#key p.version}
              <PersonForm initial={p} onsubmit={(c) => savePerson(p, c)} oncancel={() => (editing = null)} />
            {/key}
          {:else}
            <span class="name">{p.display_name}</span>
            <span class="meta">{ROLES[p.role]}{p.birth_year ? ` · born ${p.birth_year}` : ''}</span>
            <span class="actions">
              <button class="link" onclick={() => (editing = p.id)} aria-label={`Edit ${p.display_name}`}>Edit</button>
              <button class="link" onclick={() => retire(p)} aria-label={`Remove ${p.display_name}`}>Remove</button>
            </span>
          {/if}
          {#if p.role !== 'child'}
            <div class="full"><PersonAttributes person={p} onsaved={ok} onerror={problem} /></div>
          {/if}
        </li>
      {/each}
    </ul>
    {#if editing === null}<PersonForm disabled={!loaded} onsubmit={addPerson} />{/if}
  </div>

  <HomeDetailsForm nation={household?.nation} />
</section>

<style>
  .full { flex: 1 1 100%; }
  li:focus-visible { outline-offset: 4px; }
</style>
